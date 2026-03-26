#!/bin/bash

# Port to use
PORT=8210

echo "🚀 Preparing Upstox Terminal on port $PORT..."

# 1. Kill any process already using the port
PID=$(lsof -ti:$PORT)
if [ ! -z "$PID" ]; then
    echo "⚠️ Port $PORT is busy (PID: $PID). Killing it..."
    kill -9 $PID
    sleep 1
fi

# 2. Also kill port 8000 if it's left over from previous sessions
OLD_PID=$(lsof -ti:8000)
if [ ! -z "$OLD_PID" ]; then
    echo "🧹 Cleaning up port 8000..."
    kill -9 $OLD_PID
fi

# 3. Start the application
echo "✅ Starting Uvicorn on port $PORT..."
source venv/bin/activate
uvicorn app.main:app --reload --port $PORT
