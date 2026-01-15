#!/bin/bash
# Startup script for Render deployment
# This runs AFTER the persistent disk is mounted

set -e  # Exit on error

echo "🚀 Starting Federal Regulations Analyzer..."

# 1. Ensure the data directory exists on the persistent disk
echo "📁 Ensuring data directory exists..."
mkdir -p /opt/render/project/src/data

# 2. Initialize database if needed
echo "💾 Initializing database..."
python -c "import database; database.init_db(); print('✅ Database initialized')"

# 3. Check current badge count
BADGE_COUNT=$(python -c "from database import SessionLocal, DeregulationCache; db = SessionLocal(); count = db.query(DeregulationCache).count(); print(count); db.close()" 2>/dev/null || echo "0")
echo "📊 Current badges in database: $BADGE_COUNT"

# 4. Pre-compute deregulation badges if not already done (optional - only if API key is set)
if [ -n "$OPENAI_API_KEY" ]; then
    if [ "$BADGE_COUNT" -lt "100" ]; then
        echo "🤖 Pre-computing deregulation badges (cache is empty or incomplete)..."
        echo "   This will take approximately 10-15 minutes for all 153 agencies..."
        python compute_deregulation_cache.py --concurrency 5 &
        CACHE_PID=$!
        echo "   Badge computation started in background (PID: $CACHE_PID)"
        echo "   Server will start now and badges will populate over the next 10-15 minutes"
    else
        echo "✅ Badge cache already populated ($BADGE_COUNT badges found)"
    fi
else
    echo "⚠️  OPENAI_API_KEY not set - badges will be computed on-demand"
fi

# 5. Start the FastAPI server
echo "🌐 Starting FastAPI server on port ${PORT:-8000}..."
exec uvicorn api:app --host 0.0.0.0 --port ${PORT:-8000}
