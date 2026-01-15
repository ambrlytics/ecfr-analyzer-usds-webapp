"""Batch job to pre-compute deregulation badges for all agencies and cache them in the database.

Run this script daily or on-demand to keep the cache fresh.

Usage:
    python compute_deregulation_cache.py [--limit N] [--concurrency N]
"""
import asyncio
import argparse
from datetime import datetime
import database
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


async def process_agency(agency: dict, db, idx: int, total: int):
    """Process a single agency and cache its deregulation badge."""

    agency_slug = agency.get('slug')
    agency_name = agency.get('name')

    if not agency_slug:
        return {'status': 'skipped', 'name': agency_name}

    print(f"[{idx}/{total}] Processing: {agency_name}")

    try:
        # Fetch revision history for this agency (lightweight - no AI needed)
        import httpx
        from datetime import datetime, timedelta

        async with httpx.AsyncClient(timeout=120.0) as client:
            # Get agency CFR references
            agencies_resp = await client.get("https://www.ecfr.gov/api/admin/v1/agencies.json")
            if agencies_resp.status_code != 200:
                return {'status': 'failed', 'name': agency_name, 'error': 'API error'}

            agencies_list = agencies_resp.json()['agencies']
            agency = next((a for a in agencies_list if a.get('slug') == agency_slug), None)

            if not agency or not agency.get('cfr_references'):
                return {'status': 'skipped', 'name': agency_name}

            # Fetch revision history (last 12 months)
            one_year_ago = datetime.now() - timedelta(days=365)
            unique_revision_dates = set()  # Track unique dates across all titles

            for ref in agency.get('cfr_references', [])[:2]:  # Limit to first 2 for speed
                title = ref.get('title')
                if not title:
                    continue

                # Correct versioner URL format
                versioner_url = f"https://www.ecfr.gov/api/versioner/v1/versions/title-{title}.json"
                try:
                    versioner_resp = await client.get(versioner_url, timeout=30.0)

                    if versioner_resp.status_code == 200:
                        versioner_data = versioner_resp.json()

                        # Count unique revision dates in the last 12 months
                        for version in versioner_data.get('content_versions', []):
                            issue_date = version.get('issue_date')
                            if issue_date:
                                try:
                                    version_date = datetime.strptime(issue_date, '%Y-%m-%d')
                                    if version_date >= one_year_ago:
                                        unique_revision_dates.add(issue_date)
                                except:
                                    pass
                except:
                    pass  # Skip if versioner API fails for this title

            recent_revisions_count = len(unique_revision_dates)

            # If there are no revisions, skip AI analysis
            if recent_revisions_count == 0:
                likelihood, label = 'unlikely', 'Deregulation Unlikely'
                explanation = "No revisions in last 12 months"
                full_analysis = None
            else:
                # Use AI to analyze if revisions are deregulatory or administrative
                from openai import OpenAI
                import os

                openai_client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

                # Get sample of recent revision dates for context
                sorted_dates = sorted(unique_revision_dates, reverse=True)[:10]

                prompt = f"""Analyze the deregulation likelihood for {agency_name}.

Regulatory activity (last 12 months):
- {recent_revisions_count} unique revision dates
- Most recent revisions: {', '.join(sorted_dates[:5])}
- Agency oversees {len(agency.get('cfr_references', []))} CFR title(s)

Consider these factors:
1. **Revision frequency**: {recent_revisions_count} revisions suggests {"very active" if recent_revisions_count >= 20 else "moderate" if recent_revisions_count >= 10 else "minimal"} regulatory changes
2. **Recency**: Recent activity (within last 3 months) is more significant than older revisions
3. **Context**: Multiple agencies are currently revising regulations. High revision activity alone doesn't confirm deregulation.

Deregulation indicators:
- Multiple revisions to the SAME title (suggests simplification/removal)
- Very high frequency (20+ revisions) may indicate systematic review
- Recent activity after period of inactivity

Administrative update indicators:
- Low-moderate revision frequency (5-15 revisions)
- Steady, routine updates
- Technical corrections

Assess whether this pattern indicates:
- **Strong**: Clear evidence of sustained deregulatory activity (20+ revisions with recent activity)
- **Moderate**: Some evidence suggesting possible deregulation (10-19 revisions)
- **Low**: Minimal signals, could be administrative (5-9 revisions)
- **Unlikely**: Administrative updates or insufficient activity (<5 revisions)

Format your response as:
LIKELIHOOD: [strong/moderate/low/unlikely]
EXPLANATION: [1-2 sentences explaining your assessment]"""

                try:
                    response = openai_client.chat.completions.create(
                        model="gpt-4o-mini",
                        max_tokens=300,
                        messages=[{"role": "user", "content": prompt}]
                    )

                    ai_analysis = response.choices[0].message.content

                    # Parse AI response (handle markdown formatting like **Strong**)
                    if 'LIKELIHOOD:' in ai_analysis:
                        likelihood_text = ai_analysis.split('LIKELIHOOD:')[1].split('\n')[0].strip()
                        # Remove markdown formatting
                        likelihood_text = likelihood_text.replace('**', '').replace('*', '').strip().lower()
                        likelihood_match = likelihood_text
                    else:
                        likelihood_match = 'unknown'

                    explanation_match = ai_analysis.split('EXPLANATION:')[1].strip() if 'EXPLANATION:' in ai_analysis else f"{recent_revisions_count} revisions in last 12 months"

                    # Map to label
                    if likelihood_match == 'strong':
                        label = 'Strong Deregulation'
                    elif likelihood_match == 'moderate':
                        label = 'Moderate Deregulation'
                    elif likelihood_match == 'low':
                        label = 'Low Deregulation'
                    else:
                        label = 'Deregulation Unlikely'

                    likelihood = likelihood_match
                    explanation = explanation_match
                    full_analysis = ai_analysis

                except Exception as e:
                    print(f"  ⚠️  AI analysis failed, using revision count heuristic: {e}")
                    # Fallback to simple heuristic
                    if recent_revisions_count >= 10:
                        likelihood, label = 'moderate', 'Moderate Activity'
                    elif recent_revisions_count >= 5:
                        likelihood, label = 'low', 'Low Activity'
                    else:
                        likelihood, label = 'unlikely', 'Minimal Activity'
                    explanation = f"{recent_revisions_count} revisions in last 12 months (AI analysis unavailable)"
                    full_analysis = None

            # Save to database cache
            database.save_deregulation_cache(
                db,
                agency_slug=agency_slug,
                agency_name=agency_name,
                likelihood=likelihood,
                label=label,
                recent_revisions=recent_revisions_count,
                analysis=explanation,
                full_analysis=full_analysis
            )

        print(f"  ✅ Cached: {label} ({recent_revisions_count} revisions)")
        return {'status': 'success', 'name': agency_name, 'label': label}

    except Exception as e:
        print(f"  ❌ Error: {str(e)}")
        return {'status': 'failed', 'name': agency_name, 'error': str(e)}


async def compute_all_deregulation_badges(limit: int = None, concurrency: int = 10):
    """Fetch all agencies and compute their deregulation badges in parallel.

    Args:
        limit: Limit number of agencies to process (for testing)
        concurrency: Number of concurrent agency processing tasks (default: 10)
    """
    import httpx

    # Initialize database
    database.init_db()

    try:
        start_time = datetime.now()
        print(f"🔄 Starting deregulation badge computation at {start_time.isoformat()}")
        print(f"⚡ Concurrency level: {concurrency}")

        # Fetch all agencies
        async with httpx.AsyncClient(timeout=120.0) as client:
            print("📡 Fetching agencies from eCFR API...")
            agencies_resp = await client.get("https://www.ecfr.gov/api/admin/v1/agencies.json")

            if agencies_resp.status_code != 200:
                print(f"❌ Error: eCFR API returned status {agencies_resp.status_code}")
                print(f"Response: {agencies_resp.text[:500]}")
                return

            try:
                agencies_data = agencies_resp.json()
            except Exception as e:
                print(f"❌ Error parsing JSON response: {e}")
                print(f"Response content: {agencies_resp.text[:500]}")
                return

            if 'agencies' not in agencies_data:
                print(f"❌ Error: No 'agencies' key in response")
                print(f"Response keys: {list(agencies_data.keys())}")
                return

            agencies_list = agencies_data['agencies']

            # Filter agencies with CFR references
            agencies_with_refs = [
                a for a in agencies_list
                if a.get('cfr_references') and len(a.get('cfr_references', [])) > 0
            ]

            print(f"📋 Found {len(agencies_with_refs)} agencies with CFR references")

            if limit:
                agencies_with_refs = agencies_with_refs[:limit]
                print(f"⚠️  Limited to first {limit} agencies for testing")

            # Process agencies in batches with controlled concurrency
            total = len(agencies_with_refs)
            results = []

            # Use a semaphore to limit concurrency
            semaphore = asyncio.Semaphore(concurrency)

            async def process_with_semaphore(agency, idx):
                async with semaphore:
                    # Each task gets its own DB session
                    db = database.SessionLocal()
                    try:
                        return await process_agency(agency, db, idx, total)
                    finally:
                        db.close()

            # Create all tasks
            tasks = [
                process_with_semaphore(agency, idx + 1)
                for idx, agency in enumerate(agencies_with_refs)
            ]

            # Run all tasks concurrently with controlled concurrency
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Count results
            successful = sum(1 for r in results if isinstance(r, dict) and r.get('status') == 'success')
            failed = sum(1 for r in results if isinstance(r, dict) and r.get('status') == 'failed')
            skipped = sum(1 for r in results if isinstance(r, dict) and r.get('status') == 'skipped')
            errors = sum(1 for r in results if isinstance(r, Exception))

            end_time = datetime.now()
            duration = (end_time - start_time).total_seconds()

            print(f"\n{'='*60}")
            print(f"✅ Successfully cached: {successful}")
            print(f"❌ Failed: {failed}")
            print(f"⏭️  Skipped: {skipped}")
            if errors > 0:
                print(f"💥 Exceptions: {errors}")
            print(f"⏱️  Duration: {duration:.1f} seconds ({duration/60:.1f} minutes)")
            print(f"⚡ Average: {duration/total:.1f} seconds per agency")
            print(f"🕐 Completed at: {end_time.isoformat()}")
            print(f"{'='*60}")

    except Exception as e:
        print(f"Fatal error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Compute deregulation badges for all agencies")
    parser.add_argument('--limit', type=int, help='Limit number of agencies to process (for testing)')
    parser.add_argument('--concurrency', type=int, default=10, help='Number of concurrent tasks (default: 10)')
    args = parser.parse_args()

    asyncio.run(compute_all_deregulation_badges(limit=args.limit, concurrency=args.concurrency))
