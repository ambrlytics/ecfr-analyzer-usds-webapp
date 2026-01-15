#!/bin/bash
# Build script for Render deployment
# Note: The build phase does NOT have access to persistent disks
# Database initialization and badge computation happen in start.sh instead

set -e  # Exit on error

echo "🔨 Running build script..."

# Install dependencies
echo "📦 Installing dependencies..."
pip install -r requirements.txt

echo "✅ Build complete!"
echo "📝 Note: Database and badge cache will be initialized at startup (see start.sh)"
