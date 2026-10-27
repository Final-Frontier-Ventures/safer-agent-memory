"""Reference dreaming job for SQLite/`ams`-backed agents.

Run on a schedule, once per container scope:

    python dream.py --base /memory --scope tenant:acme --transcripts /logs/tenant-acme

It builds the dreaming input (curated memory, pending writes, run notes and
redacted transcripts), sends it to a model with dream-prompt.md, and stores
the returned proposals through DreamerSession (which redacts and flags them
again). It never writes memory. Review with `ams review list --base ... --scope ...`.

`call_model` is the only provider-specific part. The default uses the
Anthropic SDK (pip install anthropic); replace it for any other provider.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import uuid
from pathlib import Path

from ams import DreamerSession, SQLiteBackend
from ams.redact import redact

PROMPT = Path(__file__).with_name("dream-prompt.md").read_text(encoding="utf-8")
MAX_INPUT_CHARS = 400_000  # keep the job inside its own token budget


def call_model(system: str, user: str) -> str:
    import anthropic  # replace this function for other providers

    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=os.environ.get("AMS_DREAM_MODEL", "claude-sonnet-4-6"),
        max_tokens=8000,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(block.text for block in msg.content if block.type == "text")


def build_input(dreamer: DreamerSession, transcripts: Path | None) -> str:
    snap = dreamer.snapshot()

    def entries(rows: list[dict], label: str) -> str:
        out = []
        for e in rows:
            taint = ' tainted="true"' if e.get("tainted") else ""
            attrs = {"scope": e["scope"], "path": e["path"], "session": e.get("source_session") or ""}
            attr = " ".join(f'{k}="{html.escape(str(v), quote=True)}"' for k, v in attrs.items())
            body = html.escape(redact(e["content"])[0], quote=False)  # escaped: content cannot close the tag
            out.append(f'<entry kind="{label}" {attr}{taint}>\n{body}\n</entry>')
        return "\n".join(out) or f"(no {label} entries)"

    parts = [
        f"<curated>\n{entries(snap['active'], 'curated')}\n</curated>",
        f"<pending>\n{entries(snap['pending'], 'pending')}\n</pending>",
        f"<runs>\n{entries(snap['runs'], 'run')}\n</runs>",
    ]
    if transcripts and transcripts.exists():
        blobs = []
        for f in sorted(transcripts.glob("**/*")):
            if f.is_file():
                text, _ = redact(f.read_text(encoding="utf-8", errors="replace"))
                name = html.escape(f.name, quote=True)
                blobs.append(f'<transcript name="{name}">\n{html.escape(text, quote=False)}\n</transcript>')
        parts.append("<transcripts>\n" + "\n".join(blobs) + "\n</transcripts>")
    data = "\n\n".join(parts)
    if len(data) > MAX_INPUT_CHARS:
        data = data[:MAX_INPUT_CHARS] + "\n[input truncated; split this scope into smaller batches]"
    return f"<dreaming_input scope=\"{dreamer.scope}\">\n{data}\n</dreaming_input>"


def parse_proposals(text: str) -> list[dict]:
    """Take the fenced ```json block if present, else the first JSON list that decodes."""
    fenced = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.S)
    candidates = [fenced.group(1)] if fenced else []
    candidates += [text[m.start():] for m in re.finditer(r"\[", text)]
    decoder = json.JSONDecoder()
    for chunk in candidates:
        try:
            value, _ = decoder.raw_decode(chunk)
        except json.JSONDecodeError:
            continue
        if isinstance(value, list) and all(isinstance(v, dict) for v in value):
            return value
    return []


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--base", required=True)
    p.add_argument("--scope", required=True)
    p.add_argument("--transcripts", type=Path)
    a = p.parse_args()

    backend = SQLiteBackend(a.base, read_only=[a.scope])  # this job may not write memory
    dreamer = DreamerSession(backend, job_id=uuid.uuid4().hex[:8], scope=a.scope)
    raw = call_model(PROMPT, build_input(dreamer, a.transcripts))
    count = 0
    for item in parse_proposals(raw):
        try:
            dreamer.propose(item["path"], item["content"], item.get("description", ""),
                            evidence=item.get("evidence", []), occurrences=int(item.get("occurrences", 1)),
                            rationale=item.get("rationale", ""))
            count += 1
        except Exception as exc:  # a bad proposal must not stop the rest
            print(f"skipped {item.get('path')!r}: {exc}")
    print(f"{count} proposals stored for {a.scope}; review with: ams review list --base {a.base} --scope {a.scope}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
