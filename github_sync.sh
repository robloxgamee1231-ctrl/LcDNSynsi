#!/bin/bash
# ============================================================
# GitHub Download & Upload Script
# Uses GITHUB_TOKEN secret for authentication
# ============================================================

REPO_URL="https://x-access-token:${GITHUB_TOKEN}@github.com/robloxgamee1231-ctrl/LcDNSynsi"
BRANCH="main"

# Always point remote to authenticated URL
git remote set-url origin "$REPO_URL" 2>/dev/null || git remote add origin "$REPO_URL"

# ── Helper ───────────────────────────────────────────────────
show_help() {
  echo ""
  echo "Usage: bash github_sync.sh [command]"
  echo ""
  echo "  download   Pull latest changes from GitHub"
  echo "  upload     Push local changes to GitHub"
  echo "  status     Show what files have changed"
  echo "  help       Show this message"
  echo ""
}

# ── Download (pull) ──────────────────────────────────────────
do_download() {
  echo "⬇️  Downloading latest changes from GitHub..."
  git fetch origin "$BRANCH"
  git merge origin/"$BRANCH" --no-edit
  if [ $? -eq 0 ]; then
    echo "✅ Download complete — you are up to date."
  else
    echo "❌ Merge conflict detected. Fix conflicts, then run upload."
  fi
}

# ── Upload (push) ────────────────────────────────────────────
do_upload() {
  echo "⬆️  Uploading changes to GitHub..."

  git add -A

  if git diff --cached --quiet; then
    echo "ℹ️  Nothing new to upload — already up to date."
    exit 0
  fi

  TIMESTAMP=$(date '+%Y-%m-%d %H:%M')
  git commit -m "Sync: $TIMESTAMP"
  git push origin "$BRANCH"

  if [ $? -eq 0 ]; then
    echo "✅ Uploaded successfully at $TIMESTAMP"
  else
    echo "❌ Upload failed. Try running 'download' first to sync, then upload again."
  fi
}

# ── Status ───────────────────────────────────────────────────
do_status() {
  echo "📋 Changed files:"
  git status --short
}

# ── Entry point ──────────────────────────────────────────────
case "$1" in
  download) do_download ;;
  upload)   do_upload ;;
  status)   do_status ;;
  help|"")  show_help ;;
  *)
    echo "Unknown command: $1"
    show_help
    exit 1
    ;;
esac
