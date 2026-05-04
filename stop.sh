#!/bin/bash
# Stop both backend and frontend development servers

echo "Stopping IAPS Research App..."

PIDS=$(lsof -ti:8000,3000,3001 2>/dev/null)

if [ -z "$PIDS" ]; then
  echo "No servers are running."
else
  kill $PIDS 2>/dev/null
  echo "Backend (port 8000) and frontend (port 3000) stopped."
fi
