#!/bin/bash
# Deployment script for eCFR Analyzer

set -e  # Exit on error

echo "🚀 Starting eCFR Analyzer deployment..."

# 1. Install dependencies
echo "📦 Installing Python dependencies..."
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 2. Initialize database
echo "💾 Initializing database..."
.venv/bin/python -c "import database; database.init_db(); print('✅ Database initialized')"

# 3. Pre-compute deregulation badges (takes ~5 minutes for all 153 agencies)
echo "🤖 Pre-computing deregulation badges with AI analysis..."
echo "   This will take approximately 5-10 minutes for all 153 agencies..."
if [ -n "$OPENAI_API_KEY" ]; then
    .venv/bin/python compute_deregulation_cache.py --concurrency 10
    echo "✅ Deregulation cache populated"
else
    echo "⚠️  Warning: OPENAI_API_KEY not set - skipping AI analysis"
    echo "   Badges will be computed on-demand (slower for first-time users)"
fi

# 4. Start the server
echo "🌐 Starting FastAPI server..."
.venv/bin/uvicorn api:app --host 0.0.0.0 --port 8000

echo "✅ Deployment complete!"
