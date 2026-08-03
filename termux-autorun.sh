#!/bin/bash
# ============================================================
# Auto-Update & Keep-Alive Bot Script for Termux
# This script runs forever:
#   - Starts the bot
#   - Checks GitHub every 5 minutes for new code
#   - If new code is found, pulls it and restarts the bot
#   - If the bot crashes, restarts it automatically
# ============================================================

BOT_DIR=~/discord-bot
BOT_SCRIPT="bot.py"
CHECK_INTERVAL=300  # Check GitHub every 5 minutes (300 seconds)
GITHUB_USER="robloxgamee1231-ctrl"
REPO_NAME="LcDNSynsi"
TOKEN_FILE=~/.github_token

# ---- Load GitHub token ----
if [ -f "$TOKEN_FILE" ]; then
  GH_TOKEN=$(cat "$TOKEN_FILE")
  REPO_URL="https://x-access-token:${GH_TOKEN}@github.com/${GITHUB_USER}/${REPO_NAME}"
else
  REPO_URL="https://github.com/${GITHUB_USER}/${REPO_NAME}"
fi

# ---- First-time setup ----
if [ ! -d "$BOT_DIR" ]; then
  echo "📦 Cloning repo for the first time..."
  git clone "$REPO_URL" "$BOT_DIR"
  cd "$BOT_DIR"
  pip install -r requirements.txt
  pip install playwright
  PLAYWRIGHT_BROWSERS_PATH=0 playwright install chromium 2>/dev/null || true
fi

cd "$BOT_DIR"
git remote set-url origin "$REPO_URL"

# ---- Helper: start the bot in background ----
start_bot() {
  echo "🤖 Starting bot..."
  python "$BOT_SCRIPT" &
  BOT_PID=$!
  echo "Bot running with PID $BOT_PID"
}

# ---- Helper: stop the bot ----
stop_bot() {
  if [ ! -z "$BOT_PID" ] && kill -0 "$BOT_PID" 2>/dev/null; then
    echo "🛑 Stopping bot (PID $BOT_PID)..."
    kill "$BOT_PID"
    wait "$BOT_PID" 2>/dev/null
  fi
}

# ---- Ensure .env exists with DISCORD_TOKEN ----
if [ ! -f ".env" ] || ! grep -q "DISCORD_TOKEN" .env; then
  echo "⚠️  No DISCORD_TOKEN found in .env"
  echo "    Run: echo 'DISCORD_TOKEN=YOUR_TOKEN_HERE' > ~/discord-bot/.env"
fi

# ---- Pull latest code (stash db WAL files first) ----
git checkout -- bot_data.db-shm bot_data.db-wal 2>/dev/null || true
git rm --cached bot_data.db-shm bot_data.db-wal 2>/dev/null || true
git pull origin main --quiet
pip install -r requirements.txt --quiet
pip install playwright --quiet
PLAYWRIGHT_BROWSERS_PATH=0 playwright install chromium 2>/dev/null || true
start_bot

LAST_CHECK=$(date +%s)

# ---- Main loop ----
while true; do
  sleep 10  # Check bot health every 10 seconds

  NOW=$(date +%s)
  ELAPSED=$((NOW - LAST_CHECK))

  # Check GitHub for updates every CHECK_INTERVAL seconds
  if [ "$ELAPSED" -ge "$CHECK_INTERVAL" ]; then
    LAST_CHECK=$NOW
    echo "🔍 Checking GitHub for updates..."

    LOCAL=$(git rev-parse HEAD)
    git fetch origin main --quiet
    REMOTE=$(git rev-parse origin/main)

    if [ "$LOCAL" != "$REMOTE" ]; then
      echo "✅ New code found! Updating and restarting bot..."
      stop_bot
      # Refresh token in case it was updated
      if [ -f "$TOKEN_FILE" ]; then
        GH_TOKEN=$(cat "$TOKEN_FILE")
        git remote set-url origin "https://x-access-token:${GH_TOKEN}@github.com/${GITHUB_USER}/${REPO_NAME}"
      fi
      # Reset db WAL files so pull never aborts
      git checkout -- bot_data.db-shm bot_data.db-wal 2>/dev/null || true
      git rm --cached bot_data.db-shm bot_data.db-wal 2>/dev/null || true
      git pull origin main --quiet
      pip install -r requirements.txt --quiet
      pip install playwright --quiet
      PLAYWRIGHT_BROWSERS_PATH=0 playwright install chromium 2>/dev/null || true
      start_bot
      echo "🔄 Bot restarted with latest code!"
    else
      echo "✅ Already up to date."
    fi
  fi

  # Auto-restart if bot crashed
  if [ ! -z "$BOT_PID" ] && ! kill -0 "$BOT_PID" 2>/dev/null; then
    echo "💀 Bot crashed! Restarting in 5 seconds..."
    sleep 5
    start_bot
  fi

done
