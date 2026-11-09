#!/bin/bash
cd "$(dirname "$0")"
while true; do python3 -u logger.py >> logs/logger.log 2>&1; echo "$(date -Is) logger exited $?, restarting in 5s" >> logs/logger.log; sleep 5; done
