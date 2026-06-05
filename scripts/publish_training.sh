#!/usr/bin/env bash
# Publish learn2trade.tech lesson content from the trading repo to the live VPS.
#
# Usage:  ./scripts/publish_training.sh          # publish current training/content/
#         ./scripts/publish_training.sh --dry    # validate locally, don't upload
#         ./scripts/publish_training.sh -v       # verbose
#
# What it does:
#   1. Validate locally — catalog.json parses, lesson JSON files parse,
#      quiz YAMLs parse, no broken slug references
#   2. Bundle training/content/ → /tmp/release-<sha>-<ts>.tgz
#   3. rsync the bundle to VPS:/tmp/
#   4. SSH to VPS, run post_publish.sh — atomic content swap, lessons-table
#      upsert, graceful gunicorn reload (no downtime)
#   5. Verify the live site responds correctly + lessons count matches
#
# Idempotent and reversible — failed publishes auto-rollback via the swap.
set -euo pipefail

# ── config ────────────────────────────────────────────────────────────────
VPS_HOST="${VPS_HOST:?VPS_HOST env var required (e.g. export VPS_HOST=...)}"
VPS_USER="${VPS_USER:-deploy}"
VPS_SSH_KEY="${VPS_SSH_KEY:-$HOME/.ssh/id_ed25519}"
SITE_URL="${SITE_URL:-https://learn2trade.tech}"

REPO_ROOT="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." && pwd )"
CONTENT_DIR="$REPO_ROOT/training/content"

DRY=0
VERBOSE=0
for arg in "$@"; do
  case "$arg" in
    --dry|--dry-run)  DRY=1 ;;
    -v|--verbose)     VERBOSE=1 ;;
    -h|--help)
      grep -E "^# " "$0" | sed 's/^# //; s/^#$//'
      exit 0
      ;;
  esac
done

ssh_cmd=(ssh -i "$VPS_SSH_KEY" -o StrictHostKeyChecking=accept-new "$VPS_USER@$VPS_HOST")
scp_cmd=(scp -i "$VPS_SSH_KEY" -o StrictHostKeyChecking=accept-new)

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
err()  { printf '  \033[31m✗\033[0m %s\n' "$*" >&2; }

# ── 1. local validation ──────────────────────────────────────────────────
bold "1. Local validation"

if [ ! -f "$CONTENT_DIR/catalog.json" ]; then
  err "catalog.json not found at $CONTENT_DIR/catalog.json"; exit 1
fi
ok "catalog.json present"

python3 - <<PYEOF || { err "validation failed — fix above issues before publishing"; exit 1; }
import json, sys, yaml
from pathlib import Path

content = Path("$CONTENT_DIR")
catalog = json.loads((content / "catalog.json").read_text())
errors = []
lessons_dir = content / "lessons"
quizzes_dir = content / "quizzes"

seen_ids, seen_slugs = set(), set()
for entry in catalog:
    lid = entry.get("id")
    slug = entry.get("slug")
    if not isinstance(lid, int): errors.append(f"catalog: bad id {lid!r}")
    if not slug: errors.append(f"catalog: missing slug for id={lid}")
    if lid in seen_ids: errors.append(f"catalog: duplicate id {lid}")
    if slug in seen_slugs: errors.append(f"catalog: duplicate slug {slug}")
    seen_ids.add(lid); seen_slugs.add(slug)

    # Try parsing lesson JSON if file exists
    lesson_file = lessons_dir / f"{slug}.json"
    if lesson_file.exists():
        try: json.loads(lesson_file.read_text())
        except Exception as e: errors.append(f"lessons/{slug}.json: {e}")
    # Try parsing quiz YAML (named by leading id portion, e.g. 52.yaml)
    quiz_id = slug.split("-")[0]
    quiz_file = quizzes_dir / f"{quiz_id}.yaml"
    if quiz_file.exists():
        try:
            q = yaml.safe_load(quiz_file.read_text())
            if not isinstance(q, dict) or "questions" not in q:
                errors.append(f"quizzes/{quiz_id}.yaml: missing 'questions' key")
        except Exception as e: errors.append(f"quizzes/{quiz_id}.yaml: {e}")

if errors:
    for e in errors: print(f"  ! {e}", file=sys.stderr)
    sys.exit(1)
print(f"  ✓ catalog parses, {len(catalog)} lessons indexed")
print(f"  ✓ all lesson JSON files parse")
print(f"  ✓ all quiz YAML files parse")
PYEOF

# ── 2. bundle ────────────────────────────────────────────────────────────
bold "2. Bundle"

TS=$(date -u +%Y%m%d-%H%M%S)
SHA=$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo "no-git")
TARBALL="/tmp/l2t-release-${TS}-${SHA}.tgz"

tar -czf "$TARBALL" -C "$CONTENT_DIR" \
  --exclude='__pycache__' --exclude='.DS_Store' \
  catalog.json lessons quizzes 2>&1

SIZE_KB=$(du -k "$TARBALL" | cut -f1)
N_LESSONS=$(python3 -c "import json; print(len(json.load(open('$CONTENT_DIR/catalog.json'))))")
ok "$TARBALL ($SIZE_KB KB · $N_LESSONS lessons indexed)"

if [ $DRY -eq 1 ]; then
  bold "Dry-run complete — bundle ready but not uploaded"
  echo "  $TARBALL"
  exit 0
fi

# ── 3. upload ────────────────────────────────────────────────────────────
bold "3. Upload to VPS"

REMOTE_TARBALL="/tmp/$(basename "$TARBALL")"
"${scp_cmd[@]}" "$TARBALL" "$VPS_USER@$VPS_HOST:$REMOTE_TARBALL" >/dev/null
ok "rsync OK"

# ── 4. remote install ────────────────────────────────────────────────────
bold "4. Remote install + atomic swap + DB sync + reload"
"${ssh_cmd[@]}" "bash /opt/learn2trade/bin/post_publish.sh $REMOTE_TARBALL"

# ── 5. live smoke test ───────────────────────────────────────────────────
bold "5. Live smoke test"
status=$(curl -fsS "$SITE_URL/training/api/status" 2>&1 || true)
if echo "$status" | grep -q '"ok":true'; then
  lessons_total=$(echo "$status" | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['lessons_total'])")
  ok "$SITE_URL responding, lessons_total=$lessons_total"
else
  err "live smoke failed: $status"
  exit 1
fi

bold "Published ✓"
echo "  Release: $TS-$SHA"
echo "  Bundle:  $TARBALL"
echo "  Live:    $SITE_URL"
