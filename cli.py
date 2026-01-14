"""CLI for managing regulations data."""
import asyncio
import sys
from datetime import datetime
import database
from fetcher import fetch_agency_data
from migrate_db import migrate


async def run_ingestion(max_agencies: int = 10):
    """Fetch and store eCFR data.

    Args:
        max_agencies: Limit number of agencies to fetch (for speed)
    """
    print(f"\n🔄 Fetching data from eCFR API...")
    print(f"   Limiting to {max_agencies} agencies for speed\n")

    try:
        # Initialize database and run migrations
        database.init_db()
        migrate()

        # Fetch data
        agencies = await fetch_agency_data(max_agencies=max_agencies)

        # Save to database
        db = database.SessionLocal()

        snapshot_date = datetime.utcnow()
        count = database.save_snapshot(db, agencies, snapshot_date)

        db.close()

        print(f"\n✅ Success!")
        print(f"   Saved {count} agencies")
        print(f"   Snapshot: {snapshot_date.isoformat()}")
        print(f"   Total words: {sum(a['word_count'] for a in agencies):,}")
        print(f"\n🌐 Start the web server: python main.py")

    except Exception as e:
        print(f"\n❌ Error: {e}")
        sys.exit(1)


def main():
    """CLI entry point."""
    if len(sys.argv) < 2:
        print("Usage: python cli.py <command>")
        print("\nCommands:")
        print("  ingest [max_agencies]  - Fetch data from eCFR API (default: 10 agencies)")
        print("\nExamples:")
        print("  python cli.py ingest       # Fetch 10 agencies")
        print("  python cli.py ingest 20    # Fetch 20 agencies")
        sys.exit(1)

    command = sys.argv[1]

    if command == "ingest":
        max_agencies = int(sys.argv[2]) if len(sys.argv) > 2 else 10
        asyncio.run(run_ingestion(max_agencies))
    else:
        print(f"Unknown command: {command}")
        sys.exit(1)


if __name__ == "__main__":
    main()
