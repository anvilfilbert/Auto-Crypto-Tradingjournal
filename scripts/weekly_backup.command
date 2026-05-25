#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
REPO_ROOT="$(cd .. && pwd)"
DEST_DIR="$(pwd)"

CLAUDE_DIR="$HOME/.claude"
PROJECT_KEY="-Users-fbauer"

TS="$(date +%Y-%m-%d_%H%M%S)"
STAGING="$(mktemp -d -t journal-backup)"
PKG_NAME="journal-session_${TS}"
PKG="$STAGING/$PKG_NAME"
mkdir -p "$PKG"
trap 'rm -rf "$STAGING"' EXIT

bold() { printf "\033[1m%s\033[0m\n" "$*"; }
ok()   { printf "  \033[32m✓\033[0m %s\n" "$*"; }
warn() { printf "  \033[33m!\033[0m %s\n" "$*"; }

bold "=== Trading Journal weekly backup — $TS ==="
echo

# ─── 1. Repo source ──────────────────────────────────────────────────────────
bold "[1/7] Repo source (excludes only regenerable + DB)"
rsync -a \
  --exclude='venv/' \
  --exclude='.venv/' \
  --exclude='__pycache__/' \
  --exclude='*.pyc' \
  --exclude='.pytest_cache/' \
  --exclude='.DS_Store' \
  --exclude='node_modules/' \
  --exclude='*.db' --exclude='*.db-shm' --exclude='*.db-wal' \
  --exclude='scripts/journal-session_*.zip' \
  "$REPO_ROOT/" "$PKG/trading-journal/"
ok "repo: $(du -sh "$PKG/trading-journal" | awk '{print $1}') (with .git history, joblibs, .remember, all of .agents)"

# ─── 2. ENTIRE ~/.claude (except runtime caches) ────────────────────────────
bold "[2/7] Full ~/.claude/ (skips runtime caches only)"
if [ -d "$CLAUDE_DIR" ]; then
  rsync -a \
    --exclude='cache/' \
    --exclude='downloads/' \
    --exclude='paste-cache/' \
    --exclude='image-cache/' \
    --exclude='shell-snapshots/' \
    --exclude='telemetry/' \
    --exclude='*.lock' \
    "$CLAUDE_DIR/" "$PKG/dot-claude/"
  ok "~/.claude/: $(du -sh "$PKG/dot-claude" | awk '{print $1}')"
  ok "  · projects/: $(du -sh "$PKG/dot-claude/projects" 2>/dev/null | awk '{print $1}')"
  ok "  · plugins/:  $(du -sh "$PKG/dot-claude/plugins" 2>/dev/null | awk '{print $1}')"
  ok "  · skills/:   $(du -sh "$PKG/dot-claude/skills" 2>/dev/null | awk '{print $1}')"
  ok "  · settings.json + settings.local.json captured"
else
  warn "no ~/.claude/ found"
fi

# ─── 3. Shell config ────────────────────────────────────────────────────────
bold "[3/7] Shell config"
mkdir -p "$PKG/shell-config"
for f in .zshrc .bashrc .bash_profile .profile .zprofile; do
  if [ -f "$HOME/$f" ]; then
    cp "$HOME/$f" "$PKG/shell-config/$f"
    ok "$f captured"
  fi
done

# ─── 4. Keychain manifest (names only — values never extracted) ─────────────
bold "[4/7] Keychain manifest"
{
  echo "# Keychain entries to recreate on the new Mac"
  echo "# Generated $TS"
  echo "# Values cannot be exported by a script — re-enter them manually."
  echo
  echo "## Internet-password entries containing 'pi', 'bitget', 'anthropic', '192.168'"
  security dump-keychain 2>/dev/null | \
    grep -E '"svce"<blob>=' | \
    sort -u | \
    grep -iE 'pi|192\.168|bitget|anthropic|hermes|claude|trading' || \
    echo "(none matched the filter — run \`security dump-keychain | grep svce\` manually)"
  echo
  echo "## Restore commands (after providing values)"
  echo "security add-internet-password -a fbauer -s 192.168.1.21 -w '<PI_PASSWORD>'"
  echo "# (add more lines per service as needed)"
} > "$PKG/keychain-manifest.md"
ok "keychain-manifest.md written"

# ─── 5. pip freeze snapshot ─────────────────────────────────────────────────
bold "[5/7] Python environment snapshot"
if [ -f "$REPO_ROOT/venv/bin/pip" ]; then
  "$REPO_ROOT/venv/bin/pip" freeze > "$PKG/pip-freeze.txt" 2>/dev/null || true
  ok "pip freeze: $(wc -l < "$PKG/pip-freeze.txt" | tr -d ' ') packages"
else
  warn "no venv/bin/pip found — pip-freeze.txt skipped"
fi

# ─── 6. System inventory ────────────────────────────────────────────────────
bold "[6/7] System inventory"
{
  echo "Backup timestamp: $TS"
  echo "Host: $(hostname)"
  echo "macOS: $(sw_vers -productVersion 2>/dev/null)"
  echo "User: $(whoami)"
  echo "Repo path: $REPO_ROOT"
  echo
  echo "=== Repo git state ==="
  ( cd "$REPO_ROOT" && git log --oneline -10 2>/dev/null )
  echo
  echo "=== Repo branch + remote ==="
  ( cd "$REPO_ROOT" && git branch -vv 2>/dev/null; git remote -v 2>/dev/null )
  echo
  echo "=== Top-level package sizes inside zip ==="
  du -sh "$PKG"/* 2>/dev/null
  echo
  echo "=== Claude memory file count ==="
  find "$PKG/dot-claude/projects/$PROJECT_KEY/memory" -name '*.md' 2>/dev/null | wc -l
  echo
  echo "=== Installed brew packages (top-level) ==="
  brew leaves 2>/dev/null | head -50 || echo "(brew not found)"
} > "$PKG/inventory.txt"
ok "inventory.txt written"

# ─── 7. Restore guide ───────────────────────────────────────────────────────
bold "[7/7] Restore guide"
cat > "$PKG/RESTORE_README.md" << 'EOF'
# Trading Journal — Restore on New Mac

This zip contains everything needed to rebuild the Mac development side of this
project, INCLUDING the full Claude Code session context (memory, plugins,
session transcripts, settings, skills).

The Pi (live server at 192.168.1.21) is NOT in this backup — it runs the
database, has its own credentials, and its own backup cron at 04:00.

## Contents

| Folder / File | Purpose |
|---|---|
| `trading-journal/` | Full repo INCLUDING `.git` history, `.agents/`, all skills |
| `dot-claude/` | Full `~/.claude/` — plugins, projects/sessions, skills, settings, memory |
| `shell-config/` | `.zshrc`, `.bashrc`, etc. |
| `keychain-manifest.md` | Names of keychain entries to recreate (values must be re-entered) |
| `pip-freeze.txt` | Exact Python package versions from current venv |
| `inventory.txt` | Git log, sizes, brew packages, hostnames |

## Restore steps on a fresh macOS

### Step 1 — Prereqs
```bash
xcode-select --install
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
brew install python@3.13 rsync expect node git
```

### Step 2 — Extract into Documents
```bash
cd ~/Documents
# For .tar.xz (default):
tar -xJf /path/to/journal-session_YYYY-MM-DD_HHMMSS.tar.xz
# For .zip (fallback case only):
# unzip /path/to/journal-session_YYYY-MM-DD_HHMMSS.zip
cd journal-session_YYYY-MM-DD_HHMMSS
```

(Or simply double-click the archive in Finder — macOS Archive Utility extracts both .tar.xz and .zip natively.)

### Step 3 — Restore shell config
```bash
cp shell-config/.zshrc ~/.zshrc 2>/dev/null
cp shell-config/.bashrc ~/.bashrc 2>/dev/null
exec zsh                                       # reload
```

### Step 4 — Restore ~/.claude/
```bash
# CAREFUL: this overwrites anything already in ~/.claude/
mkdir -p ~/.claude
rsync -a dot-claude/ ~/.claude/
```

### Step 5 — Restore the repo
```bash
mkdir -p ~/Documents/ClaudeAIData
cp -R trading-journal ~/Documents/ClaudeAIData/Trading-Journal
cd ~/Documents/ClaudeAIData/Trading-Journal

# Recreate venv (exact same packages)
python3.13 -m venv venv
source venv/bin/activate
pip install -r pip-freeze.txt    # or requirements.txt if you maintain one
```

### Step 6 — Restore keychain entries
Open `keychain-manifest.md`. For each entry, re-enter the password manually:
```bash
security add-internet-password -a fbauer -s 192.168.1.21 -w '<PI_PASSWORD>'
# repeat for each entry listed
```

### Step 7 — Verify
```bash
# Verify Claude memory loaded
cat ~/.claude/projects/-Users-fbauer/memory/MEMORY.md | head -20

# Verify repo git history matches the backup
cd ~/Documents/ClaudeAIData/Trading-Journal
git log --oneline -5

# Test SSH to Pi
ssh fbauer@192.168.1.21 'echo OK · date'

# Run test suite
python3 -m pytest tests/ -q
```

## What's NOT in the backup (and why)

- `venv/` — exact contents regenerable from `pip-freeze.txt`
- `*.db` — Pi owns the live DB; Mac has none of value
- `.env` — secrets are Pi-only; on Mac just stub vars for testing
- `~/.claude/cache/`, `downloads/`, `paste-cache/`, `image-cache/`,
  `shell-snapshots/`, `telemetry/` — runtime junk, Claude rebuilds these
- macOS keychain values — Apple prevents script export (security feature)
- Pi server contents — has its own backup at `/home/fbauer/trading-journal/backups/`

## Note on session transcripts

The full `~/.claude/projects/-Users-fbauer/` directory IS included, with all
session JSONL files (this can grow to hundreds of MB over time). If you ever
need to rotate old transcripts, move them to an archive folder before running
the backup script.
EOF
ok "RESTORE_README.md written"

# ─── Compress (tar.xz preferred, zip fallback) ──────────────────────────────
echo
bold "[compress] tar + xz (LZMA — best ratio for JSON/text) ..."
ARCHIVE_PATH="$DEST_DIR/${PKG_NAME}.tar.xz"

# XZ_OPT=-9e = maximum compression (slower but ~3-5× smaller than zip on text)
if XZ_OPT='-9e -T0' tar -cJf "$ARCHIVE_PATH" -C "$STAGING" "$PKG_NAME" 2>/dev/null; then
  ok "tar.xz compression succeeded"
else
  warn "tar.xz failed — falling back to zip -9"
  ARCHIVE_PATH="$DEST_DIR/${PKG_NAME}.zip"
  ( cd "$STAGING" && zip -rq9X "$ARCHIVE_PATH" "$PKG_NAME" )
fi
ARCHIVE_SIZE=$(du -h "$ARCHIVE_PATH" | awk '{print $1}')

echo
bold "=== Done ==="
echo "  Archive:    $ARCHIVE_PATH"
echo "  Size:       $ARCHIVE_SIZE"
echo "  Next step:  copy this file to iCloud Drive manually"
echo
echo "  All backups in this folder:"
ls -lh "$DEST_DIR"/journal-session_*.{tar.xz,zip} 2>/dev/null | awk '{print "    "$9" ("$5")"}'
echo
read -n 1 -s -r -p "Press any key to close..." || true
echo
