"""Database migration to add parent/child agency columns."""
import sqlite3
from pathlib import Path

DB_PATH = Path("data/regulations.db")

def migrate():
    """Add parent_agency and child_agencies columns if they don't exist."""
    if not DB_PATH.exists():
        print("No existing database found. Will create new schema.")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Check if columns exist
    cursor.execute("PRAGMA table_info(agency_snapshots)")
    columns = [row[1] for row in cursor.fetchall()]

    changes_made = False

    if 'parent_agency' not in columns:
        print("Adding parent_agency column...")
        cursor.execute("ALTER TABLE agency_snapshots ADD COLUMN parent_agency TEXT")
        changes_made = True

    if 'child_agencies' not in columns:
        print("Adding child_agencies column...")
        cursor.execute("ALTER TABLE agency_snapshots ADD COLUMN child_agencies TEXT")
        changes_made = True

    if changes_made:
        conn.commit()
        print("✓ Migration complete!")
    else:
        print("✓ Database already up to date.")

    conn.close()

if __name__ == "__main__":
    migrate()
