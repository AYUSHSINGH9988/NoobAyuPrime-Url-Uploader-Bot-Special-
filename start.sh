#!/bin/bash

# 1. Purane Aria2 processes ko saaf karna (Safe side ke liye)
pkill -9 aria2c

# 2. Start Aria2c Daemon in Background
# Fix: Added --file-allocation=none to prevent crashes on large torrents
echo "🚀 Starting Aria2c with optimization..."
aria2c --enable-rpc --rpc-listen-port=6800 --daemon=true \
--allow-overwrite=true --seed-time=0 --file-allocation=none \
--max-connection-per-server=16 --split=10 --min-split-size=10M

# 3. Wait 3 seconds to ensure it's running
sleep 3

# 4. Start the Bot
echo "🤖 Starting Bot..."
python3 main.py
