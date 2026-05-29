#!/usr/bin/env bash
# Quick "are people using the site" report.
# Pulls live numbers from the VPS — no caching, no auth needed beyond SSH.
set -euo pipefail

VPS_HOST="${VPS_HOST:?VPS_HOST env var required (e.g. export VPS_HOST=...)}"
VPS_USER="${VPS_USER:-deploy}"
VPS_SSH_KEY="${VPS_SSH_KEY:-$HOME/.ssh/id_ed25519}"

ssh -i "$VPS_SSH_KEY" "$VPS_USER@$VPS_HOST" bash <<'REMOTE_EOF'
DB=/opt/learn2trade/db/learn2trade.db
sql() { sudo -u training sqlite3 "$DB" "$@"; }

printf "\033[1m=== learn2trade.tech — site status ===\033[0m\n"

# ── Users ──
total=$(sql "SELECT COUNT(*) FROM users WHERE is_active=1 AND deleted_at IS NULL")
admin=$(sql "SELECT COUNT(*) FROM users WHERE is_admin=1 AND is_active=1")
real=$((total - admin))

last_24h=$(sql "SELECT COUNT(*) FROM users WHERE created_at >= datetime('now','-24 hours') AND is_admin=0")
last_7d=$(sql "SELECT COUNT(*) FROM users WHERE created_at >= datetime('now','-7 days') AND is_admin=0")

active_24h=$(sql "SELECT COUNT(*) FROM users WHERE last_login_at >= datetime('now','-24 hours') AND is_admin=0")
active_7d=$(sql "SELECT COUNT(*) FROM users WHERE last_login_at >= datetime('now','-7 days') AND is_admin=0")

printf "\n\033[1mUsers\033[0m\n"
printf "  Real users (excl. admin):   %d\n" "$real"
printf "  Admin accounts:             %d\n" "$admin"
printf "  New in last 24h:            %d\n" "$last_24h"
printf "  New in last 7 days:         %d\n" "$last_7d"
printf "  Logged in last 24h:         %d\n" "$active_24h"
printf "  Logged in last 7 days:      %d\n" "$active_7d"

# ── Engagement ──
total_passes=$(sql "SELECT COUNT(*) FROM lesson_progress WHERE status='passed'")
total_attempts=$(sql "SELECT COUNT(*) FROM quiz_attempts")
avg_lessons_passed=$(sql "SELECT printf('%.1f', AVG(c)) FROM (SELECT COUNT(*) AS c FROM lesson_progress WHERE status='passed' GROUP BY user_id)")
[ -z "$avg_lessons_passed" ] && avg_lessons_passed="0"

printf "\n\033[1mEngagement\033[0m\n"
printf "  Total lessons passed:       %d (across all users)\n" "$total_passes"
printf "  Total quiz answers:         %d\n" "$total_attempts"
printf "  Avg lessons passed/user:    %s\n" "$avg_lessons_passed"

# ── Support tickets ──
open_tickets=$(sql "SELECT COUNT(*) FROM support_tickets WHERE status != 'closed'")
unread_admin=$(sql "SELECT COUNT(*) FROM support_tickets WHERE unread_admin = 1")

printf "\n\033[1mSupport\033[0m\n"
printf "  Open tickets:               %d\n" "$open_tickets"
printf "  Awaiting your reply:        %d\n" "$unread_admin"

# ── Recent activity ──
printf "\n\033[1mLast 10 audit events\033[0m\n"
sql "SELECT ts, event, COALESCE(email, '-') FROM audit_log ORDER BY id DESC LIMIT 10" \
  | awk -F'|' '{printf "  %s  %-18s %s\n", $1, $2, $3}'

# ── Latest users ──
printf "\n\033[1mLatest 5 signups\033[0m\n"
sql "SELECT created_at, email, login_count FROM users WHERE is_admin=0 ORDER BY id DESC LIMIT 5" \
  | awk -F'|' '{printf "  %s  %-30s  %s logins\n", $1, $2, $3}'

REMOTE_EOF
