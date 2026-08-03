#!/data/data/com.termux/files/usr/bin/sh
# This file auto-starts the bot when your phone reboots
# Place this file at: ~/.termux/boot/start-bot.sh

cd ~/discord-bot

# Set your Discord token here (only needed once)
export DISCORD_TOKEN="paste-your-token-here"

# Start the auto-update & keep-alive script
bash termux-autorun.sh >> ~/bot.log 2>&1 &
