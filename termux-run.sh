#!/bin/bash
# ============================================================
# Run this on Termux to pull latest code and start the bot
# ============================================================

GITHUB_USER="robloxgamee1231-ctrl"
REPO_NAME="LcDNSynsi"
BOT_DIR=~/discord-bot
TOKEN_FILE=~/.github_token

# ---- Load GitHub token ----
if [ -f "$TOKEN_FILE" ]; then
  GH_TOKEN=$(cat "$TOKEN_FILE")
  REPO_URL="https://x-access-token:${GH_TOKEN}@github.com/${GITHUB_USER}/${REPO_NAME}"
else
  echo "⚠️  No token found at ~/.github_token"
  echo "    Run this once to save it:"
  echo "    echo 'YOUR_TOKEN_HERE' > ~/.github_token"
  REPO_URL="https://github.com/${GITHUB_USER}/${REPO_NAME}"
fi

# First time: clone the repo
if [ ! -d "$BOT_DIR" ]; then
  echo "Cloning repo for the first time..."
  git clone "$REPO_URL" "$BOT_DIR"
  cd "$BOT_DIR"
  pip install -r requirements.txt
  pip install playwright
  PLAYWRIGHT_BROWSERS_PATH=0 playwright install chromium 2>/dev/null || true
else
  echo "Pulling latest changes from GitHub..."
  cd "$BOT_DIR"
  git remote set-url origin "$REPO_URL"
  git checkout -- bot_data.db-shm bot_data.db-wal 2>/dev/null || true
  git rm --cached bot_data.db-shm bot_data.db-wal 2>/dev/null || true
  git pull origin main
  pip install -r requirements.txt --quiet
  pip install playwright --quiet
  PLAYWRIGHT_BROWSERS_PATH=0 playwright install chromium 2>/dev/null || true
fi

# ---- Ensure .env exists ----
if [ ! -f "$BOT_DIR/.env" ]; then
  echo ""
  echo "⚠️  No .env file found! The bot needs your Discord token."
  echo "    Run this command (replace YOUR_TOKEN with your actual token):"
  echo "    echo 'DISCORD_TOKEN=YOUR_TOKEN' > ~/discord-bot/.env"
  echo ""
fi

echo "Starting bot..."
python bot.py
