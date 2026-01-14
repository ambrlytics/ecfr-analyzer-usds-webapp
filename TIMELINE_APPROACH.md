# Timeline Feature - How It Works

## The Problem with "Instant" Historical Data

The eCFR API provides a versioner endpoint (`/api/versioner/v1/versions/title-{num}.json`) that lists when a title was updated, but it **does not provide the actual historical metrics** (word count, complexity score, etc.) for those dates.

To get actual historical metrics, we would need to:
1. Fetch the version dates for a title
2. For EACH historical date, download the full XML for that date
3. Parse the XML and compute metrics

**This is extremely slow and API-intensive** - for 10 agencies across multiple titles with quarterly snapshots over 5 years, we'd be making hundreds of API calls and parsing gigabytes of XML data.

## The Correct Approach

The timeline feature works by **accumulating real snapshots over time through multiple ingestion runs**:

### First Run
```bash
python cli.py ingest 10
```
- Fetches current data for 10 agencies
- Stores one snapshot in the database
- Timeline shows 1 entry per agency

### Second Run (days/weeks later)
```bash
python cli.py ingest 10
```
- Fetches current data again (may have changed)
- Stores a NEW snapshot alongside the first
- Timeline now shows 2 entries per agency
- Changes tab can compare the two snapshots

### Over Time
Each ingestion run adds a new snapshot to the database. After months of running:
- Timeline shows the evolution of regulations over time
- Changes tab tracks actual regulatory changes
- Word count and complexity trends become visible

## Why This Is Better

1. **Real Data**: Each snapshot represents actual metrics at that point in time, not fabricated historical data
2. **Performance**: One ingestion = ~2-3 minutes, not hours of historical fetching
3. **API-Friendly**: Stays within rate limits, doesn't overwhelm the eCFR API
4. **Accurate**: Shows actual regulatory changes, not estimated historical values

## How to Build Historical Data

To quickly populate the timeline for demo purposes:

```bash
# Run ingestion 3 times with a few seconds between each
python cli.py ingest 5
sleep 10
python cli.py ingest 5
sleep 10
python cli.py ingest 5
```

This creates 3 distinct snapshots in the database. The timeline will show all 3, even though they're close together in time.

## Alternative: Automated Scheduled Runs

For production use, set up a cron job or scheduled task:

```bash
# Run daily at 2am
0 2 * * * cd /path/to/ecfr-analyzer && python cli.py ingest 10
```

This builds up a natural timeline of regulatory changes over weeks and months.
