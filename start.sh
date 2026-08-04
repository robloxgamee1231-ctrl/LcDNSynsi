#!/bin/bash
cd /home/z/my-project/LcDNSynsi
source venv/bin/activate
exec python3 -u bot.py > bot.log 2>&1
