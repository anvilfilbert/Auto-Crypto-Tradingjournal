#!/usr/bin/env bash
# Deploy app code (not content) to learn2trade.tech VPS.
#
# Use for: changes to app/, migrations/, requirements.txt — anything in the
# Flask app itself. Lesson content goes via publish_training.sh instead.
#
# Pipeline:
#   1. rsync learn2trade/app/ + migrations/ + requirements.txt → VPS
#   2. Re-install Python deps if requirements.txt changed
#   3. Run any new SQL migrations
#   4. Graceful gunicorn reload (no downtime)
#   5. Smoke test /health
#
# Usage:
#   ./scripts/deploy_app.sh
#   ./scripts/deploy_app.sh --dry      # diff only, don't apply
#   ./scripts/deploy_app.sh --restart  # full restart instead of graceful reload
set -euo pipefail

VPS_HOST="${VPS_HOST:?VPS_HOST env var required (e.g. export VPS_HOST=...)}"
VPS_USER="${VPS_USER:-deploy}"
VPS_SSH_KEY="${VPS_SSH_KEY:-$HOME/.ssh/id_ed25519}"
SITE_URL="${SITE_URL:-https://learn2trade.tech}"

REPO_ROOT="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." && pwd )"
APP_LOCAL="$REPO_ROOT/learn2trade"
APP_REMOTE="/opt/learn2trade/app"

DRY=0
RESTART=0
for arg in "$@"; do
  case "$arg" in
    --dry|--dry-run)  DRY=1 ;;
    --restart)        RESTART=1 ;;
    -h|--help)
      grep -E "^# " "$0" | sed 's/^# //; s/^#$//'
      exit 0
      ;;
  esac
done

ssh_cmd=(ssh -i "$VPS_SSH_KEY" -o StrictHostKeyChecking=accept-new "$VPS_USER@$VPS_HOST")

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
err()  { printf '  \033[31m✗\033[0m %s\n' "$*" >&2; }

# ── 1. rsync code (NOT content — that's published separately) ────────────
bold "1. Rsync code"

rsync_flags=(-az --delete
  --exclude='__pycache__'
  --exclude='*.pyc'
  --exclude='.DS_Store'
  --exclude='*.db'
  --exclude='*.db-*'
  --exclude='content'
  --exclude='promo'
  --exclude='.venv'
  --exclude='tests'
  -e "ssh -i $VPS_SSH_KEY -o StrictHostKeyChecking=accept-new")
[ $DRY -eq 1 ] && rsync_flags+=(--dry-run -i)

# Sync the entire learn2trade/ tree to /opt/learn2trade/app/
# (server-side will chown to training afterwards)
rsync "${rsync_flags[@]}" "$APP_LOCAL/" "deploy@$VPS_HOST:/tmp/l2t-app-staging/"
ok "files rsynced to /tmp/l2t-app-staging/"

if [ $DRY -eq 1 ]; then
  bold "Dry-run done — no changes applied"
  exit 0
fi

# ── 2-5. all on VPS (one ssh round trip) ─────────────────────────────────
bold "2-5. Install + migrate + reload"
"${ssh_cmd[@]}" "RESTART=$RESTART bash -s" <<'REMOTE_EOF'
set -euo pipefail

APP_DIR=/opt/learn2trade/app
RUN_USER=training

# Atomic swap: app → app.old, staging → app
BACKUP=/opt/learn2trade/app.old_$(date -u +%Y%m%d-%H%M%S)
sudo mv "$APP_DIR" "$BACKUP"
sudo mv /tmp/l2t-app-staging "$APP_DIR"
sudo chown -R $RUN_USER:$RUN_USER "$APP_DIR"
echo "  swapped → $APP_DIR (prev → $BACKUP)"

# Reuse the existing venv (it's in app.old now), move it back
if [ -d "$BACKUP/.venv" ]; then
  sudo mv "$BACKUP/.venv" "$APP_DIR/.venv"
  echo "  venv preserved"
fi

# Re-install deps if requirements.txt content changed
if ! sudo cmp -s "$BACKUP/requirements.txt" "$APP_DIR/requirements.txt"; then
  echo "  requirements.txt changed — reinstalling"
  sudo -u $RUN_USER "$APP_DIR/.venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"
fi

# Apply any new SQL migrations (init_db() re-runs all of them; idempotent)
# Triggered automatically by the app on next boot.

# Reload (or restart if requested)
if [ "${RESTART:-0}" = "1" ]; then
  sudo systemctl restart learn2trade
  echo "  service restarted"
else
  sudo systemctl reload learn2trade
  echo "  service reloaded (graceful)"
fi
sleep 2

# Smoke test /health
http=$(curl -fsS -o /dev/null -w "%{http_code}" http://127.0.0.1:5050/health 2>&1 || echo "fail")
if [ "$http" != "200" ]; then
  echo "  ✗ /health → $http — rolling back"
  sudo rm -rf "$APP_DIR"
  sudo mv "$BACKUP" "$APP_DIR"
  sudo systemctl restart learn2trade
  exit 1
fi
echo "  /health → 200"

# Prune old app backups (keep last 3)
keep=3
old_count=$(ls -1d /opt/learn2trade/app.old_* 2>/dev/null | wc -l)
if [ "$old_count" -gt "$keep" ]; then
  ls -1dt /opt/learn2trade/app.old_* | tail -n +$((keep + 1)) | xargs sudo rm -rf
  echo "  pruned old app backups (kept last $keep)"
fi
REMOTE_EOF

# ── 6. live smoke test from outside ──────────────────────────────────────
bold "6. Live smoke test"
status=$(curl -fsS "$SITE_URL/training/api/status" 2>&1 || true)
if echo "$status" | grep -q '"ok":true'; then
  ok "$SITE_URL → ok"
else
  err "live smoke failed: $status"
  exit 1
fi

bold "Deployed ✓"
