#!/bin/bash
cd "$(dirname "$0")"; mkdir -p logs data
if [ -f logs/loop.pid ] && kill -0 "$(cat logs/loop.pid)" 2>/dev/null; then echo "already running (pid $(cat logs/loop.pid))"; exit 0; fi
setsid nohup ./run_loop.sh > /dev/null 2>&1 < /dev/null & echo $! > logs/loop.pid
echo "started (pid $!)"
