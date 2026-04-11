#!/bin/bash

echo "🚀 Starting Aria2c..."
aria2c --enable-rpc --rpc-listen-port=6800 --daemon --allow-overwrite=true --seed-time=0

sleep 3

echo "🤖 Starting Bot..."
python3 main.py
