#!/bin/bash
# Start both backend and frontend development servers

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "🚀 Starting IAPS Research App..."
echo ""

# Check for .env
if [ ! -f "$SCRIPT_DIR/backend/.env" ]; then
  echo "⚠️  No .env file found in backend/."
  echo "   Copy backend/.env.example to backend/.env and add your API keys:"
  echo "   - ANTHROPIC_API_KEY (get at console.anthropic.com)"
  echo "   - TAVILY_API_KEY (get at app.tavily.com)"
  echo ""
fi

# Start backend
echo "📡 Starting backend (FastAPI) on http://localhost:8000 ..."
cd "$SCRIPT_DIR/backend"
./venv/bin/uvicorn main:app --reload --port 8000 &
BACKEND_PID=$!

# Wait a moment for backend to start
sleep 2

# Start frontend
echo "🌐 Starting frontend (Next.js) on http://localhost:3000 ..."
cd "$SCRIPT_DIR/frontend"
npm run dev &
FRONTEND_PID=$!

echo ""
echo "✅ Both servers started."
echo "   Frontend: http://localhost:3000"
echo "   Backend API docs: http://localhost:8000/docs"
echo ""
echo "Press Ctrl+C to stop both servers."

# Wait for either to exit
trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit 0" INT TERM
wait
