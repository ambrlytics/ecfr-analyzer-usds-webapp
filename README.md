# Federal Regulations Analyzer

A web application to track and analyze federal regulatory activity across 150+ government agencies using data from the Electronic Code of Federal Regulations (eCFR) API.

**⚠️ Disclaimer:** This is not an official U.S. government website. This tool is for informational and analytical purposes only.

## Features

### Government-Wide Trends Dashboard
- **Change Frequency**: Monthly regulatory revision activity over the last 12 months
- **Cumulative Changes**: 5-year view of accumulated regulatory changes
- Interactive charts with hover tooltips showing detailed data
- Top 10 most active CFR titles displayed

### Agency Analysis
- Browse 150+ federal agencies and their sub-agencies
- Search by agency name, sub-agency, or CFR title number
- View CFR references for each agency
- Word count estimates for regulatory text

### Deregulation Signals (Optional)
- AI-powered analysis to identify potential deregulation activity
- Requires OpenAI API key for badge computation
- Can pre-compute badges during deployment or compute on-demand

## Quick Start

### Local Development

1. **Clone and Install**
   ```bash
   cd ecfr-analyzer-usds
   python3 -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   ```

2. **Set Environment Variables (Optional)**
   ```bash
   cp .env.example .env
   # Edit .env and add your OpenAI API key if using AI analysis
   ```

3. **Start the Server**
   ```bash
   uvicorn api:app --reload --port 8000
   ```

4. **Open in Browser**
   ```
   http://localhost:8000
   ```

### Production Deployment

Use the provided deployment script:

```bash
chmod +x deploy.sh
export OPENAI_API_KEY="your-key-here"  # Optional
./deploy.sh
```

The deployment script will:
1. Install Python dependencies in a virtual environment
2. Initialize the SQLite database
3. Pre-compute deregulation badges (if API key provided)
4. Start the FastAPI server on port 8000

## Architecture

```
api.py              - FastAPI backend with REST endpoints
database.py         - SQLite models for caching deregulation analysis
static/index.html   - Single-page web application
deploy.sh          - Production deployment script
compute_deregulation_cache.py - Pre-compute AI analysis badges
```

## API Endpoints

### Statistics
- `GET /api/overview/stats` - Get total agencies, sub-agencies, and regulations count
- `GET /api/trends/titles` - Get 12-month frequency and 5-year cumulative trends data

### eCFR Data
- `GET /api/ecfr/agencies` - List all agencies with CFR references
  - Query param: `include_word_counts=true` (optional)

### Deregulation Analysis
- `GET /api/deregulation/badge/{agency_slug}` - Get deregulation badge for an agency
  - Returns cached badge if available, otherwise computes on-demand

## Data Sources

- **eCFR API**: https://www.ecfr.gov/developer/api/v1
  - Agencies: `https://www.ecfr.gov/api/admin/v1/agencies.json`
  - Structure: `https://www.ecfr.gov/api/versioner/v1/structure/{date}/title-{num}.json`
  - Versions: `https://www.ecfr.gov/api/versioner/v1/versions/title-{num}.json`

## Configuration

### Environment Variables

- `OPENAI_API_KEY` - OpenAI API key for deregulation analysis (optional)

### Database

- Uses SQLite for caching deregulation badges
- Database file: `regulations.db`
- Automatically created on first run

## Deployment Platforms

This application can be deployed to:

### Cloud Platforms
- **Heroku**: Add `Procfile` with `web: uvicorn api:app --host 0.0.0.0 --port $PORT`
- **Render**: Use `uvicorn api:app --host 0.0.0.0 --port $PORT` as start command
- **Railway**: Auto-detects Python and uses requirements.txt
- **Fly.io**: Create `fly.toml` configuration

### VPS/Server
- Use the provided `deploy.sh` script
- Consider using a process manager like systemd or supervisord
- Set up nginx as reverse proxy

### Docker (Optional)
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
EXPOSE 8000
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
```

## Development

### Project Structure
```
.
├── api.py                          # FastAPI application
├── database.py                     # SQLite database models
├── compute_deregulation_cache.py   # Pre-compute AI badges
├── deploy.sh                       # Deployment script
├── requirements.txt                # Python dependencies
├── static/
│   └── index.html                 # Frontend SPA
└── regulations.db                 # SQLite database (created at runtime)
```

### Adding New Features

1. **New API Endpoints**: Add to `api.py`
2. **Frontend Changes**: Edit `static/index.html`
3. **Database Changes**: Modify `database.py` models

## Performance Notes

- First load of overview stats takes ~30-60 seconds (fetches all 50 CFR title structures)
- Trends endpoint takes ~10-15 seconds (fetches version history for 50 titles)
- Deregulation badge computation with AI takes ~2-3 seconds per agency
- Pre-computing badges for all 153 agencies takes ~5-10 minutes

## License

This project is open source and available for public use.

## Support

For issues or questions, please open an issue on the project repository.
