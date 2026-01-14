#!/bin/bash
# Build script for Render deployment

set -e  # Exit on error

echo "🔨 Running build script..."

# 1. Install dependencies
echo "📦 Installing dependencies..."
pip install -r requirements.txt

# 2. Initialize database
echo "💾 Initializing database..."
python -c "import database; database.init_db(); print('✅ Database initialized')"

# 3. Pre-compute deregulation badges (optional - only if API key is set)
if [ -n "$OPENAI_API_KEY" ]; then
    echo "🤖 Pre-computing deregulation badges..."
    echo "   This will take approximately 5-10 minutes for all 153 agencies..."
    python compute_deregulation_cache.py --concurrency 10
    echo "✅ Deregulation cache populated"
else
    echo "⚠️  OPENAI_API_KEY not set - skipping badge pre-computation"
    echo "   Badges will be computed on-demand when users click on agencies"
fi

echo "✅ Build complete!"
