#!/bin/bash
# ============================================================
# Auto-sync to GitHub
# Run this on Replit whenever you want to push changes
# Or it runs automatically via the bot workflow
# ============================================================

cd /home/runner/workspace

# Add all changes
git add -A

# Check if there's anything to commit
if git diff --cached --quiet; then
  echo "Nothing new to push — already up to date."
  exit 0
fi

# Commit with timestamp
TIMESTAMP=$(date '+%Y-%m-%d %H:%M')
git commit -m "Auto-sync: $TIMESTAMP"

# Push to GitHub (uses GITHUB_TOKEN for auth)
git remote set-url origin "https://x-access-token:${GITHUB_TOKEN}@github.com/robloxgamee1231-ctrl/LcDNSynsi"
git push origin main

echo "✅ Pushed to GitHub at $TIMESTAMP"
