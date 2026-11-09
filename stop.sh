#!/bin/bash
cd "$(dirname "$0")"; [ -f logs/loop.pid ] && kill -- -"$(cat logs/loop.pid)" 2>/dev/null; rm -f logs/loop.pid; echo stopped
