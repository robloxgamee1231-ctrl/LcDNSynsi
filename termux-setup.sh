#!/bin/bash
# ============================================================
# Termux Setup Script — Discord Bot
# Run this once on Termux to set up everything
# ============================================================

echo "=== Setting up Discord Bot on Termux ==="

# Update packages
pkg update -y && pkg upgrade -y

# Install required packages
pkg install -y python git chromium

# Install pip packages
pip install -r requirements.txt

# Install Playwright and point it at the system Chromium
pip install playwright
PLAYWRIGHT_BROWSERS_PATH=0 playwright install chromium 2>/dev/null || true

echo ""
echo "=== Setup complete! ==="
echo ""
echo "To run the bot:"
echo "  python bot.py"
echo ""
echo "To keep it running 24/7 (even when Termux is closed):"
echo "  1. Install Termux:Boot from F-Droid"
echo "  2. Open Termux:Boot once"
echo "  3. Run: mkdir -p ~/.termux/boot"
echo "  4. Run: nano ~/.termux/boot/start-bot.sh"
echo "  5. Paste the following into that file:"
echo "     #!/data/data/com.termux/files/usr/bin/sh"
echo "     cd ~/discord-bot"
echo "     bash termux-autorun.sh >> ~/bot.log 2>&1 &"
echo "  6. Save with Ctrl+X, then: chmod +x ~/.termux/boot/start-bot.sh"
echo ""
echo "Your bot will now auto-start whenever your phone reboots!"
