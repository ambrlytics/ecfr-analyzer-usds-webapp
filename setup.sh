#!/bin/bash

echo "🏛️  Federal Regulations Analyzer - Setup"
echo "=========================================="
echo ""

# Install dependencies
echo "📦 Installing dependencies..."
pip install -r requirements.txt

echo ""
echo "✅ Setup complete!"
echo ""
echo "Next steps:"
echo "  1. Fetch data: python cli.py ingest"
echo "  2. Start server: python main.py"
echo "  3. Open http://localhost:8000"
echo ""
