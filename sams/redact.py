"""Redaction and review flags for dreaming proposals (STANDARD.md sections 4.1 and 5).

Redaction is deterministic and runs before any proposal is stored (C9). It is a
safety net, not a guarantee: keep the patterns tuned to your environment and
extend SECRET_PATTERNS for your own token formats.

Flags mark proposals that need extra scrutiny (C10). Flagging never blocks a
proposal; the owner decides.
"""

from __future__ import annotations

import re

REDACTED = "[REDACTED:{kind}]"

# Token prefixes are matched without a leading word boundary ("tokenghp_..." is
# still caught). Order matters: specific formats first, generic ones last.
_NB = r"(?<![A-Za-z0-9])"  # not preceded by alphanumerics, but '_' or a word is fine for prefixed tokens
SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----(?:.*?-----END [A-Z ]*PRIVATE KEY-----|.*)", re.S)),
    ("aws-access-key", re.compile(r"(?:AKIA|ASIA)[0-9A-Z]{16}(?![0-9A-Z])")),
    ("github-token", re.compile(r"(?:ghp|gho|ghu|ghs|ghr|github_pat)_[A-Za-z0-9_]{20,}")),
    ("gitlab-token", re.compile(r"glpat-[A-Za-z0-9_-]{20,}")),
    ("slack-token", re.compile(r"xox[abeprs]-[A-Za-z0-9-]{10,}")),
    ("slack-webhook", re.compile(r"https://hooks\.slack\.com/services/[A-Za-z0-9/_-]+")),
    ("stripe-key", re.compile(r"(?:sk|pk|rk)_(?:live|test)_[A-Za-z0-9]{16,}")),
    ("api-key", re.compile(_NB + r"(?:sk|pk|rk)-[A-Za-z0-9_-]{16,}")),
    ("google-api-key", re.compile(r"AIza[0-9A-Za-z_-]{35}")),
    ("google-oauth", re.compile(r"ya29\.[0-9A-Za-z_-]{20,}")),
    ("npm-token", re.compile(r"npm_[A-Za-z0-9]{36}")),
    ("huggingface-token", re.compile(r"hf_[A-Za-z0-9]{30,}")),
    ("sendgrid-key", re.compile(r"SG\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}")),
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    ("auth-header", re.compile(r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{12,}")),
    ("azure-key", re.compile(r"(?i)(?:AccountKey|SharedAccessKey)=[A-Za-z0-9+/=]{20,}")),
    ("url-credentials", re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s@]*:[^/\s@]+@")),
    (
        "assigned-secret",
        re.compile(
            r"(?i)([A-Za-z0-9_]*(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|credential)[A-Za-z0-9_]*)"
            r"(['\"]?\s*[:=\uff1a]\s*)(?:\"[^\"\n]{3,}\"|'[^'\n]{3,}'|[^\s'\",}]{4,})"
        ),
    ),
    ("spoken-password", re.compile(r"(?i)\b(pass(?:word|phrase)|pwd)\s+(?:is|was)\s+(?=\S*[0-9_!@#$%^&*])\S{6,}")),
    ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("card-number", re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")),
    ("ssn", re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")),
    ("phone", re.compile(r"(?<![\w+])\+\d{1,3}[ -]?\(?\d{1,4}\)?(?:[ -]?\d{2,4}){2,4}(?!\d)")),
]


_INVISIBLE = re.compile("[\u00ad\u200b-\u200f\u2060-\u2064\ufeff]")


def redact(text: str) -> tuple[str, int]:
    """Return (redacted_text, number_of_redactions).

    Invisible formatting characters are removed first so they cannot split a
    token past the patterns. Known limits: secrets in free prose without a
    recognisable keyword or format, and non-ASCII email addresses.
    """
    text = _INVISIBLE.sub("", text)
    count = 0

    def _sub(kind: str):
        def inner(m: re.Match[str]) -> str:
            nonlocal count
            count += 1
            if kind == "url-credentials":
                return f"{m.group(1)}{REDACTED.format(kind=kind)}@"
            if kind == "assigned-secret":
                return f"{m.group(1)}{m.group(2)}{REDACTED.format(kind=kind)}"
            return REDACTED.format(kind=kind)

        return inner

    for kind, pattern in SECRET_PATTERNS:
        text = pattern.sub(_sub(kind), text)
    return text, count


INSTRUCTION_PATTERNS = [
    re.compile(p, re.I)
    for p in (
        r"\bignore (all |any )?(previous|prior|above|earlier) (instructions|rules|guidance)",
        r"\bdisregard (all |the )?(previous|prior|above|system)",
        r"\byou (are|must) now\b",
        r"\b(new|updated|override) (system )?(instructions|rules|policy)\b",
        r"\bdo not (tell|inform|alert) the (user|owner|operator)\b",
        r"\b(always|never) (approve|accept|allow|trust|run|execute)\b",
        r"<\s*/?\s*(system|instructions?)\s*>",
    )
]

SENSITIVE_CHANGE_PATTERNS: dict[str, re.Pattern[str]] = {
    "url": re.compile(r"\bhttps?://|\b[a-z0-9-]+\.(?:com|net|org|io|ai|dev|sh|xyz)\b", re.I),
    "command": re.compile(r"(?m)^\s*(?:\$|>|#!)|\b(?:curl|wget|bash|sh|sudo|chmod|rm|pip|npm|docker|ssh|nc|powershell)\b"),
    "credentials": re.compile(r"(?i)\b(password|credential|secret|token|api[_ -]?key|auth)\w*"),
    "scope": re.compile(r"(?i)\b(in[- ]scope|out[- ]of[- ]scope|scope|allow[- ]?list|deny[- ]?list|target)\b"),
    "permissions": re.compile(r"(?i)\b(permission|allowedtools|deny|sudo|admin|root|role|grant|bypass)\b"),
}


def review_flags(text: str) -> list[str]:
    """Return sorted flags for a proposed change; empty list means routine."""
    flags = {name for name, p in SENSITIVE_CHANGE_PATTERNS.items() if p.search(text)}
    if any(p.search(text) for p in INSTRUCTION_PATTERNS):
        flags.add("instruction-like")
    return sorted(flags)
