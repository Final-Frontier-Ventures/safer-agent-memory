#!/usr/bin/env bash
# Weekly transcript dreaming for a repo developed with Claude Code (STANDARD.md section 7).
#
#   dream.sh /path/to/repo [days]
#
# 1. Copies the repo's Claude Code transcripts from the last N days and redacts them.
# 2. Creates a git worktree on branch dream/<date>.
# 3. Runs Claude Code headless with the dreaming prompt; it commits proposals to that branch only.
# 4. Never pushes and never touches the main branch. Review the branch like any PR.
#
# Cron example (Mondays 07:13):
#   13 7 * * 1  /opt/sams/dream.sh /srv/repos/myproject 7 >> /var/log/sams-dream.log 2>&1
set -euo pipefail

REPO="$(cd "${1:?usage: dream.sh REPO [DAYS]}" && pwd)"
DAYS="${2:-7}"
DATE="$(date +%Y-%m-%d)"
BRANCH="dream/${DATE}"
HERE="$(cd "$(dirname "$0")" && pwd)"

# Claude Code stores transcripts under ~/.claude/projects/<repo path with non-alphanumerics as '-'>.
# Override TRANSCRIPTS_DIR if your installation differs.
ENCODED="$(printf '%s' "$REPO" | sed 's/[^A-Za-z0-9]/-/g')"
TRANSCRIPTS_DIR="${TRANSCRIPTS_DIR:-$HOME/.claude/projects/$ENCODED}"
[ -d "$TRANSCRIPTS_DIR" ] || { echo "no transcripts at $TRANSCRIPTS_DIR"; exit 0; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
mapfile -t FILES < <(find "$TRANSCRIPTS_DIR" -name '*.jsonl' -mtime "-$DAYS" -type f)
[ "${#FILES[@]}" -gt 0 ] || { echo "no sessions in the last $DAYS days"; exit 0; }
sams redact --out "$WORK/transcripts" "${FILES[@]}"

cd "$REPO"
git fetch --quiet 2>/dev/null || true
WT="$REPO/../.dream-$(basename "$REPO")-$DATE"
git worktree add -B "$BRANCH" "$WT" HEAD >/dev/null
trap 'rm -rf "$WORK"; git -C "$REPO" worktree remove --force "$WT" 2>/dev/null || true' EXIT

PROMPT="$(cat <<EOF
$(cat "$HERE/../dreaming/dream-prompt.md")

## This run

- Scope: project memory of this repository (CLAUDE.md files, memory/, .claude/skills/, notes/).
- Redacted transcripts from the last $DAYS days are in $WORK/transcripts (JSONL, one file per session).
- Instead of returning JSON, apply each proposal as an edit in this worktree and make one git commit per
  proposal. Commit message: first line = the description; body = rationale, occurrence count and evidence.
- You are on branch $BRANCH. Do not switch branches, push, or edit .claude/settings.json.
EOF
)"

cd "$WT"
BASE="$(git rev-parse HEAD)"
# The repo's deny rules protect curated files from everyday sessions. This worktree exists only to
# draft proposals for review, so set them aside here (never in the main checkout). skip-worktree
# stops `git add -A` from staging the missing file as a deletion.
if [ -f .claude/settings.json ]; then
  git update-index --skip-worktree .claude/settings.json
  mv .claude/settings.json "$WORK/settings.json"
fi

claude -p "$PROMPT" \
  --add-dir "$WORK/transcripts" \
  --permission-mode acceptEdits \
  --allowedTools "Read,Grep,Glob,Edit,Write,Bash(git add:*),Bash(git commit:*),Bash(git diff:*),Bash(git status:*)"

# Write the list to a file before grepping: with pipefail, `git diff | grep -q` can return 141
# (SIGPIPE) and silently skip this check.
git diff --name-only "$BASE" HEAD > "$WORK/changed.txt"
if grep -qE '(^|/)(\.claude/(settings(\.local)?\.json|commands/|agents/|hooks/)|\.mcp\.json$)' "$WORK/changed.txt"; then
  echo "dreaming run touched agent configuration (settings, hooks, commands, agents or .mcp.json); discarding branch $BRANCH" >&2
  cd "$REPO"
  git -C "$REPO" worktree remove --force "$WT"; git -C "$REPO" branch -D "$BRANCH" >/dev/null
  exit 1
fi

N="$(git rev-list --count HEAD ^"$BASE")"
cd "$REPO"
git worktree remove --force "$WT"
if [ "$N" -eq 0 ]; then
  git branch -D "$BRANCH" >/dev/null
  echo "no proposals this week"
else
  echo "$N proposals on branch $BRANCH; review with: git -C $REPO log -p main..$BRANCH"
fi
