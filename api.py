"""FastAPI backend for regulations analyzer."""
from datetime import datetime, timedelta
from typing import List, Optional, Dict
from fastapi import FastAPI, Depends, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
import database
import httpx
import asyncio
import os
import json
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

app = FastAPI(title="Federal Regulations Analyzer")

# Initialize database
database.init_db()

# Global cache for CFR word counts loaded from file
# Format: {'fetched_at': str, 'agencies': [], 'title_word_counts': {}}
CFR_CACHE = None
CACHE_FILE = "cfr_word_counts_cache.json"

# In-memory cache for expensive API calls (1 hour TTL)
STATS_CACHE = {"data": None, "timestamp": None}
TRENDS_CACHE = {"data": None, "timestamp": None}
CACHE_TTL = 3600  # 1 hour in seconds


def classify_deregulation_likelihood(ai_analysis: str, recent_revisions_count: int) -> tuple:
    """Strictly classify deregulation likelihood based on AI analysis.

    Returns: (likelihood, label) tuple

    Classification rules (in priority order):
    1. STRONG: Explicit "strong deregulation" mention + high revisions (10+)
    2. MODERATE: Explicit positive deregulation language + some revisions (3-9)
    3. LOW: Minor signals or low revision activity (1-2)
    4. UNLIKELY: Explicit negative language or no activity
    """
    analysis_lower = ai_analysis.lower()

    # PRIORITY 1: Check for explicit UNLIKELY signals first
    if any(phrase in analysis_lower for phrase in [
        'deregulation unlikely',
        'no clear deregulation',
        'no deregulation signals',
        'minimal deregulation activity',
        'not actively deregulating',
        'increased regulatory burden',
        'adding requirements'
    ]):
        return 'unlikely', 'Deregulation Unlikely'

    # PRIORITY 2: Check for STRONG deregulation (must have explicit phrase + high activity)
    has_strong_phrase = any(phrase in analysis_lower for phrase in [
        'strong deregulation signals',
        'active deregulation',
        'significant deregulation activity',
        'coordinated deregulation effort'
    ])

    if has_strong_phrase and recent_revisions_count >= 10:
        return 'strong', 'Strong Deregulation'

    # PRIORITY 3: Check for MODERATE deregulation (clear positive signals + medium activity)
    has_moderate_phrase = any(phrase in analysis_lower for phrase in [
        'moderate deregulation',
        'some deregulation signals',
        'streamlining efforts',
        'reducing regulatory burden',
        'discretionary language increases'
    ])

    if (has_moderate_phrase or has_strong_phrase) and 3 <= recent_revisions_count < 10:
        return 'moderate', 'Moderate Deregulation'

    # PRIORITY 4: LOW deregulation (minor signals or low revision count)
    has_minor_signals = any(phrase in analysis_lower for phrase in [
        'potential deregulation',
        'possible simplification',
        'flexibility',
        'discretion'
    ])

    if has_minor_signals or 1 <= recent_revisions_count <= 2:
        return 'low', 'Low Deregulation'

    # DEFAULT: If no clear signals, mark as unlikely
    return 'unlikely', 'Deregulation Unlikely'


def load_cfr_cache():
    """Load pre-fetched CFR data from cache file."""
    global CFR_CACHE
    if CFR_CACHE is not None:
        return CFR_CACHE

    import json

    if os.path.exists(CACHE_FILE):
        print(f"Loading CFR cache from {CACHE_FILE}...")
        with open(CACHE_FILE, 'r') as f:
            CFR_CACHE = json.load(f)
        print(f"✓ Cache loaded (fetched at {CFR_CACHE.get('fetched_at')})")
        return CFR_CACHE
    else:
        print(f"⚠ No cache file found at {CACHE_FILE}")
        print("Run: python3 prefetch_word_counts.py")
        return None


@app.get("/")
async def root():
    """Serve main UI."""
    return FileResponse("static/index.html")


@app.get("/api/agencies")
async def get_agencies(db: Session = Depends(database.get_db)) -> List[str]:
    """Get list of all agencies."""
    return database.get_all_agencies(db)


@app.get("/api/ecfr/agencies")
async def get_ecfr_agencies(include_word_counts: bool = True):
    """Fetch all agencies with word counts from structure API."""
    async with httpx.AsyncClient(timeout=120.0) as client:
        # Fetch agencies with error handling
        try:
            agencies_resp = await client.get("https://www.ecfr.gov/api/admin/v1/agencies.json")
            agencies_resp.raise_for_status()  # Raise error for bad status codes
            agencies_list = agencies_resp.json()['agencies']
        except httpx.HTTPStatusError as e:
            raise HTTPException(status_code=503, detail=f"eCFR API error: {e.response.status_code}")
        except Exception as e:
            raise HTTPException(status_code=503, detail=f"Failed to fetch agencies from eCFR API: {str(e)}")

        # Get word counts if requested
        if include_word_counts:
            # Collect all unique titles across agencies and children
            titles = {ref['title'] for a in agencies_list for ref in a.get('cfr_references', []) if ref.get('title')}
            titles.update({ref['title'] for a in agencies_list for c in a.get('children', [])
                          for ref in c.get('cfr_references', []) if ref.get('title')})

            # Fetch structure metadata for all titles (fast - just JSON)
            title_data = {}
            for title_num in titles:
                try:
                    url = f"https://www.ecfr.gov/api/versioner/v1/structure/2025-01-01/title-{title_num}.json"
                    struct = (await client.get(url, timeout=30.0)).json()

                    # Extract sizes for title and chapters
                    title_data[str(title_num)] = {
                        'size': struct.get('size', 0),
                        'chapters': {ch['identifier']: ch.get('size', 0)
                                   for ch in struct.get('children', [])
                                   if ch.get('type') in ['subtitle', 'chapter']}
                    }
                except:
                    title_data[str(title_num)] = {'size': 0, 'chapters': {}}

            # Helper to add word counts to CFR references
            def enrich_refs(refs):
                for ref in refs:
                    title = str(ref.get('title', ''))
                    chapter = ref.get('chapter')
                    td = title_data.get(title, {})

                    # Use chapter size if available, else full title
                    size = td['chapters'].get(chapter, td.get('size', 0))
                    ref['word_count'] = size // 6  # bytes to words approximation
                return refs

            # Enrich all agencies and children
            for agency in agencies_list:
                agency['cfr_references'] = enrich_refs(agency.get('cfr_references', []))
                for child in agency.get('children', []):
                    child['cfr_references'] = enrich_refs(child.get('cfr_references', []))

    # Format response
    return {
        'agencies': sorted([{
            'name': a['name'],
            'slug': a['slug'],
            'parent_name': None,
            'children': a.get('children', []),
            'cfr_references': a.get('cfr_references', [])
        } for a in agencies_list if a.get('name') and a.get('slug')], key=lambda x: x['name']),
        'total': len(agencies_list)
    }


@app.get("/api/overview/stats")
async def get_overview_stats():
    """Get overview statistics for dashboard."""
    # Check cache first
    import time
    if STATS_CACHE["data"] and STATS_CACHE["timestamp"]:
        age = time.time() - STATS_CACHE["timestamp"]
        if age < CACHE_TTL:
            print(f"📦 Returning cached stats (age: {int(age)}s)")
            return STATS_CACHE["data"]

    print("🔄 Fetching fresh stats...")
    async with httpx.AsyncClient(timeout=120.0) as client:
        try:
            # Fetch agencies
            agencies_resp = await client.get("https://www.ecfr.gov/api/admin/v1/agencies.json")
            agencies_resp.raise_for_status()
            agencies_list = agencies_resp.json()['agencies']

            # Count agencies and sub-agencies
            total_agencies = len(agencies_list)
            total_sub_agencies = sum(len(a.get('children', [])) for a in agencies_list)

            # Count total CFR sections across all 50 titles
            total_sections = 0

            # Recursively count sections in the structure
            def count_sections(node):
                count = 0
                if node.get('type') == 'section':
                    count = 1
                for child in node.get('children', []):
                    count += count_sections(child)
                return count

            # Fetch structure for each CFR title and count sections
            for title_num in range(1, 51):
                try:
                    url = f"https://www.ecfr.gov/api/versioner/v1/structure/2025-01-01/title-{title_num}.json"
                    struct_resp = await client.get(url, timeout=30.0)

                    if struct_resp.status_code == 200:
                        struct_data = struct_resp.json()
                        total_sections += count_sections(struct_data)
                except Exception as e:
                    print(f"Error fetching title {title_num}: {e}")
                    continue

            result = {
                'total_agencies': total_agencies,
                'total_sub_agencies': total_sub_agencies,
                'total_regulations': total_sections
            }

            # Cache the result
            import time
            STATS_CACHE["data"] = result
            STATS_CACHE["timestamp"] = time.time()
            print(f"✅ Stats cached")

            return result
        except Exception as e:
            raise HTTPException(status_code=503, detail=f"Failed to fetch stats: {str(e)}")


@app.get("/api/snapshot/latest")
async def get_latest_snapshot(db: Session = Depends(database.get_db)):
    """Get latest snapshot date and data."""
    latest_date = database.get_latest_snapshot(db)

    if not latest_date:
        raise HTTPException(status_code=404, detail="No snapshots found")

    agencies = database.get_agencies_by_snapshot(db, latest_date)

    return {
        'snapshot_date': latest_date.isoformat(),
        'agencies': [a.to_dict() for a in agencies],
        'summary': {
            'total_agencies': len(agencies),
            'total_words': sum(a.word_count for a in agencies),
            'avg_complexity': round(
                sum(a.complexity_score for a in agencies) / len(agencies), 2
            ) if agencies else 0
        }
    }


@app.get("/api/agency/{agency_name}/history")
async def get_agency_history(agency_name: str, db: Session = Depends(database.get_db)):
    """Get historical data for an agency."""
    history = database.get_agency_history(db, agency_name)

    if not history:
        raise HTTPException(status_code=404, detail="Agency not found")

    return {
        'agency_name': agency_name,
        'snapshots': [h.to_dict() for h in history]
    }


@app.get("/api/agency/{agency_name}/changes")
async def get_agency_changes(agency_name: str, db: Session = Depends(database.get_db)):
    """Get changes between latest snapshots for an agency."""
    changes = database.calculate_changes(db, agency_name)

    if not changes:
        raise HTTPException(
            status_code=404,
            detail="Not enough snapshots to calculate changes"
        )

    return changes


@app.get("/api/agency/{slug}/explain")
async def explain_agency(slug: str):
    """Use AI to explain what an agency regulates based on CFR references."""
    # Get OpenAI API key
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=503,
            detail="AI service not configured. Please set OPENAI_API_KEY environment variable."
        )

    # Fetch agency data from eCFR API
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            agencies_resp = await client.get("https://www.ecfr.gov/api/admin/v1/agencies.json")
            agencies_list = agencies_resp.json()['agencies']

            # Find the requested agency
            agency = None
            for a in agencies_list:
                if a.get('slug') == slug:
                    agency = a
                    break
                # Also check children
                for child in a.get('children', []):
                    if child.get('slug') == slug:
                        agency = child
                        agency['parent_name'] = a.get('name')
                        break
                if agency:
                    break

            if not agency:
                raise HTTPException(status_code=404, detail="Agency not found")

            # Build context from CFR references
            cfr_refs = agency.get('cfr_references', [])
            if not cfr_refs:
                return {
                    'agency_name': agency.get('name'),
                    'slug': slug,
                    'explanation': f"{agency.get('name')} does not have any CFR references in the eCFR database."
                }

            # Format CFR references for the prompt
            refs_text = []
            for ref in cfr_refs:
                title = ref.get('title', 'Unknown')
                chapter = ref.get('chapter', '')
                if chapter:
                    refs_text.append(f"Title {title}, Chapter {chapter}")
                else:
                    refs_text.append(f"Title {title}")

            # Construct prompt for AI
            prompt = f"""Based on the following Code of Federal Regulations (CFR) references, explain in 2-3 sentences what {agency.get('name')} regulates and its primary responsibilities.

CFR References:
{chr(10).join('- ' + ref for ref in refs_text)}

Provide a concise, informative explanation suitable for a general audience."""

            # Call OpenAI API
            try:
                openai_response = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json"
                    },
                    json={
                        "model": "gpt-4o-mini",
                        "messages": [
                            {"role": "system", "content": "You are a helpful assistant that explains federal agency responsibilities based on their Code of Federal Regulations references."},
                            {"role": "user", "content": prompt}
                        ],
                        "temperature": 0.7,
                        "max_tokens": 300
                    },
                    timeout=30.0
                )

                if openai_response.status_code != 200:
                    error_detail = openai_response.text
                    # Check for specific OpenAI errors
                    if openai_response.status_code == 401:
                        raise HTTPException(status_code=503, detail="OpenAI API authentication failed. Please check your API key.")
                    elif openai_response.status_code == 429:
                        raise HTTPException(status_code=503, detail="OpenAI API rate limit exceeded. Please try again later.")
                    else:
                        raise HTTPException(status_code=502, detail=f"AI service error: {error_detail}")

                result = openai_response.json()
                explanation = result['choices'][0]['message']['content'].strip()
            except httpx.TimeoutException:
                raise HTTPException(status_code=504, detail="OpenAI API request timed out. Please try again.")
            except KeyError as e:
                raise HTTPException(status_code=502, detail=f"Unexpected OpenAI API response format: {str(e)}")

            return {
                'agency_name': agency.get('name'),
                'slug': slug,
                'parent_agency': agency.get('parent_name'),
                'cfr_references': refs_text,
                'explanation': explanation
            }

        except httpx.HTTPError as e:
            raise HTTPException(status_code=502, detail=f"Error fetching agency data: {str(e)}")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error generating explanation: {str(e)}")


@app.get("/api/deregulation/signals")
async def get_deregulation_signals(db: Session = Depends(database.get_db)):
    """Use AI to analyze 10-year regulation trends with complexity analysis and trend charts."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=503,
            detail="AI service not configured. Please set OPENAI_API_KEY environment variable."
        )

    async with httpx.AsyncClient(timeout=300.0) as client:
        try:
            # Fetch agencies
            agencies_resp = await client.get("https://www.ecfr.gov/api/admin/v1/agencies.json")
            agencies_list = agencies_resp.json()['agencies']

            # Select top agencies by regulation size for analysis
            agencies_to_analyze = [a for a in agencies_list if a.get('cfr_references')][:15]

            # Generate 10-year timeline (yearly snapshots)
            from datetime import datetime as dt
            current_year = dt.now().year
            timeline_years = list(range(current_year - 10, current_year + 1))

            print(f"Analyzing {len(agencies_to_analyze)} agencies over {len(timeline_years)} years...")

            # Collect historical data for each agency
            agency_trends = []

            for agency in agencies_to_analyze:
                agency_name = agency.get('name')
                agency_slug = agency.get('slug')

                # Track word counts and complexity over time
                timeline_data = []

                # Get CFR titles this agency regulates
                titles = set()
                for ref in agency.get('cfr_references', [])[:2]:  # Limit to first 2 for performance
                    if ref.get('title'):
                        titles.add(ref['title'])

                if not titles:
                    continue

                print(f"  Analyzing {agency_name}...")

                # Fetch historical snapshots
                for year in timeline_years:
                    snapshot_date = f"{year}-01-15"  # Mid-January each year

                    total_words = 0
                    all_text = []

                    for title in titles:
                        try:
                            # Fetch title XML for this date
                            url = f"https://www.ecfr.gov/api/versioner/v1/full/{snapshot_date}/title-{title}.xml"
                            response = await client.get(url, timeout=60.0)

                            if response.status_code == 200:
                                from bs4 import BeautifulSoup
                                soup = BeautifulSoup(response.text, 'xml')

                                # Get relevant chapters for this agency
                                for ref in agency.get('cfr_references', []):
                                    if ref.get('title') == title:
                                        chapter_id = ref.get('chapter')
                                        if chapter_id:
                                            # Find specific chapter
                                            for chapter_elem in soup.find_all(['CHAPTER', 'DIV3']):
                                                if chapter_elem.get('N') == chapter_id:
                                                    chapter_text = chapter_elem.get_text(separator=' ', strip=True)
                                                    total_words += len(chapter_text.split())
                                                    all_text.append(chapter_text)
                                                    break
                                        else:
                                            # Use full title
                                            full_text = soup.get_text(separator=' ', strip=True)
                                            total_words += len(full_text.split())
                                            all_text.append(full_text)

                            await asyncio.sleep(1.5)  # Rate limiting
                        except Exception as e:
                            print(f"    Error fetching {year} data for Title {title}: {e}")
                            continue

                    # Calculate complexity score
                    combined_text = ' '.join(all_text)
                    complexity = calculate_complexity_score(combined_text) if combined_text else 0

                    timeline_data.append({
                        'year': year,
                        'word_count': total_words,
                        'complexity_score': complexity
                    })

                if timeline_data:
                    # Calculate 10-year changes
                    first_point = timeline_data[0]
                    last_point = timeline_data[-1]

                    word_count_change = last_point['word_count'] - first_point['word_count']
                    word_count_pct = (word_count_change / first_point['word_count'] * 100) if first_point['word_count'] > 0 else 0
                    complexity_change = last_point['complexity_score'] - first_point['complexity_score']

                    agency_trends.append({
                        'agency_name': agency_name,
                        'agency_slug': agency_slug,
                        'timeline': timeline_data,
                        'word_count_change': word_count_change,
                        'word_count_pct_change': round(word_count_pct, 2),
                        'complexity_change': round(complexity_change, 2),
                        'current_word_count': last_point['word_count'],
                        'current_complexity': last_point['complexity_score']
                    })

            # Generate trend chart data
            chart_data = {
                'years': timeline_years,
                'agencies': []
            }

            for trend in agency_trends[:8]:  # Top 8 agencies for chart
                chart_data['agencies'].append({
                    'name': trend['agency_name'],
                    'word_counts': [point['word_count'] for point in trend['timeline']],
                    'complexity_scores': [point['complexity_score'] for point in trend['timeline']]
                })

            # Use AI to generate narrative analysis
            summary_data = [{
                'agency': t['agency_name'],
                'word_change': t['word_count_change'],
                'pct_change': t['word_count_pct_change'],
                'complexity_change': t['complexity_change']
            } for t in agency_trends[:10]]

            prompt = f"""Analyze 10-year federal regulation trends (from {timeline_years[0]} to {timeline_years[-1]}) and write a compelling narrative.

Trend Data Summary:
{json.dumps(summary_data, indent=2)}

Context:
- Word count changes show whether regulations grew or shrank
- Complexity scores measure regulatory burden (obligations, prohibitions, penalties)
- Negative changes suggest deregulation; positive changes suggest increased regulation

Write a 3-paragraph narrative (6-8 sentences total) covering:

1. **Overall Trends**: What are the major patterns across agencies? Are we seeing net regulation or deregulation? Which timeframes show the most change?

2. **Notable Agencies**: Which agencies had the most significant changes? What might explain these patterns (policy shifts, reform periods, administrations)?

3. **Implications**: What do these trends mean for regulatory burden on businesses and citizens? Any concerning patterns or positive developments?

Be specific with numbers, name agencies, and identify timeframes. Write in a professional but accessible tone."""

            try:
                response = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json"
                    },
                    json={
                        "model": "gpt-4o-mini",
                        "messages": [
                            {"role": "system", "content": "You are a regulatory policy analyst who writes clear, data-driven narratives about federal regulation trends. Focus on facts and patterns in the data."},
                            {"role": "user", "content": prompt}
                        ],
                        "temperature": 0.7,
                        "max_tokens": 800
                    },
                    timeout=30.0
                )

                if response.status_code == 200:
                    result = response.json()
                    narrative = result['choices'][0]['message']['content'].strip()
                else:
                    narrative = "AI analysis unavailable - please review the chart and table data above."
            except Exception as e:
                narrative = f"Unable to generate narrative analysis. Please review the data visualization and table below for insights into regulatory trends."

            # Sort agencies by absolute word count change for table
            sorted_trends = sorted(agency_trends, key=lambda x: abs(x['word_count_change']), reverse=True)

            return {
                'chart_data': chart_data,
                'narrative': narrative,
                'agencies': sorted_trends,
                'analysis_period': f"{timeline_years[0]}-{timeline_years[-1]}",
                'total_agencies_analyzed': len(agency_trends)
            }

        except Exception as e:
            print(f"Error in deregulation signals analysis: {e}")
            raise HTTPException(status_code=500, detail=f"Error analyzing trends: {str(e)}")


def calculate_complexity_score(text: str) -> float:
    """Calculate regulatory complexity score from text."""
    if not text:
        return 0.0

    import re
    text_lower = text.lower()
    words = text.split()
    total_words = len(words)

    if total_words == 0:
        return 0.0

    # Count regulatory indicators
    modal_verbs = len(re.findall(r'\b(shall|must|may|should|required)\b', text_lower))
    cross_refs = len(re.findall(r'(§|CFR|\bcfr\b)', text))
    legal_terms = len(re.findall(r'\b(penalty|violation|compliance|fine|sanction)\b', text_lower))
    exceptions = len(re.findall(r'\b(except|unless|provided that|notwithstanding)\b', text_lower))

    # Normalize per 1000 words and weight components
    complexity = (
        (modal_verbs / total_words * 1000) * 0.4 +
        (cross_refs / total_words * 1000) * 0.25 +
        (legal_terms / total_words * 1000) * 0.2 +
        (exceptions / total_words * 1000) * 0.15
    )

    return round(complexity, 2)


@app.get("/api/deregulation/deep-analysis")
async def get_deep_deregulation_analysis(
    agency_slug: str,
    db: Session = Depends(database.get_db)
):
    """Deep analysis of regulation text using AI to detect deregulation signals."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=503,
            detail="AI service not configured. Please set OPENAI_API_KEY environment variable."
        )

    # Fetch agency CFR references
    async with httpx.AsyncClient(timeout=120.0) as client:
        try:
            # Get agency info
            agencies_resp = await client.get("https://www.ecfr.gov/api/admin/v1/agencies.json")
            agencies_list = agencies_resp.json()['agencies']

            agency = None
            for a in agencies_list:
                if a.get('slug') == agency_slug:
                    agency = a
                    break
                for child in a.get('children', []):
                    if child.get('slug') == agency_slug:
                        agency = child
                        break
                if agency:
                    break

            if not agency or not agency.get('cfr_references'):
                raise HTTPException(status_code=404, detail="Agency not found or has no CFR references")

            # Fetch actual regulation text from versioner API
            print(f"Fetching regulation text for {agency.get('name')}...")
            regulation_samples = []
            revision_history = []

            # Limit to first 2 CFR references for speed
            for ref in agency.get('cfr_references', [])[:2]:
                title = ref.get('title')
                chapter = ref.get('chapter')

                if title and chapter:
                    try:
                        # Fetch version history to get revision dates
                        versions_url = f"https://www.ecfr.gov/api/versioner/v1/versions/title-{title}.json"
                        versions_resp = await client.get(versions_url, timeout=30.0)
                        if versions_resp.status_code == 200:
                            versions_data = versions_resp.json()
                            content_versions = versions_data.get('content_versions', [])

                            # Get recent corrections/revisions (last 12 months)
                            from datetime import datetime as dt
                            one_year_ago = dt.now().replace(year=dt.now().year - 1)

                            for version in content_versions[-10:]:  # Last 10 versions
                                issue_date = version.get('issue_date')
                                if issue_date:
                                    try:
                                        version_date = dt.strptime(issue_date, '%Y-%m-%d')
                                        if version_date >= one_year_ago:
                                            revision_history.append({
                                                'title': title,
                                                'date': issue_date,
                                                'identifier': version.get('identifier', ''),
                                                'volume': version.get('volume', '')
                                            })
                                    except:
                                        pass

                        # Fetch structure to get section IDs
                        structure_url = f"https://www.ecfr.gov/api/versioner/v1/structure/2025-01-01/title-{title}.json"
                        structure = (await client.get(structure_url, timeout=30.0)).json()

                        # Find the chapter in structure
                        chapter_node = None
                        for node in structure.get('children', []):
                            if node.get('identifier') == chapter:
                                chapter_node = node
                                break

                        if chapter_node:
                            # Get first few sections from this chapter
                            sections_found = 0
                            for subpart in chapter_node.get('children', [])[:3]:  # First 3 subparts
                                for section in subpart.get('children', [])[:2]:  # First 2 sections each
                                    if sections_found >= 5:  # Limit to 5 sections total
                                        break

                                    section_label = section.get('label')
                                    if section_label:
                                        # Fetch actual section text
                                        section_url = f"https://www.ecfr.gov/api/versioner/v1/full/2025-01-01/title-{title}.xml"

                                        # For now, use structure data
                                        regulation_samples.append({
                                            'title': title,
                                            'chapter': chapter,
                                            'section': section_label,
                                            'label_text': section.get('label', ''),
                                            'reserved': section.get('reserved', False)
                                        })
                                        sections_found += 1

                    except Exception as e:
                        print(f"Error fetching CFR {title} Ch. {chapter}: {e}")
                        continue

            if not regulation_samples:
                return {
                    'agency_name': agency.get('name'),
                    'analysis': 'Unable to fetch regulation text for analysis.',
                    'signal_words': {}
                }

            # Define signal words for different regulatory patterns
            signal_categories = {
                'obligations': ['shall', 'must', 'required', 'obligated', 'mandatory'],
                'prohibitions': ['shall not', 'prohibited', 'forbidden', 'may not'],
                'penalties': ['penalty', 'fine', 'sanction', 'violation', 'enforcement'],
                'discretion': ['may', 'discretion', 'appropriate', 'reasonable'],
                'exceptions': ['except', 'unless', 'provided that', 'notwithstanding'],
                'cross_references': ['pursuant to', 'in accordance with', 'as defined in', 'CFR', '§']
            }

            # Use AI to analyze the structure and detect signals
            revision_context = ""
            if revision_history:
                revision_context = f"""

Recent Revisions/Corrections (Last 12 months):
{json.dumps(revision_history, indent=2)}

Note: Analyze if recent revisions correlate with deregulation activity."""

            prompt = f"""Analyze the following Code of Federal Regulations structure for {agency.get('name')} and identify regulatory burden signals, including WHEN deregulation may have occurred.

Agency: {agency.get('name')}
CFR References: {', '.join([f"Title {r.get('title')} Chapter {r.get('chapter')}" for r in agency.get('cfr_references', [])])}

Sample regulation sections:
{json.dumps(regulation_samples[:10], indent=2)}
{revision_context}

Analyze for these signal categories:
1. **Obligations** (shall, must, required) - indicate mandatory compliance
2. **Prohibitions** (shall not, prohibited) - indicate restrictions
3. **Penalties** (penalty, fine, violation) - indicate enforcement burden
4. **Discretion** (may, discretion) - indicate flexibility
5. **Exceptions** (except, unless) - indicate complexity
6. **Cross-references** (pursuant to, §) - indicate interconnectedness

Provide:
1. Overall regulatory burden assessment (High/Medium/Low)
2. Key signal patterns found
3. Specific evidence of regulatory burden or flexibility
4. **TIMING: When did deregulation activity begin?** Based on revision dates, identify specific timeframes when changes occurred
5. Deregulation signals (if any): areas where language suggests reduced burden
6. Comparison to typical agency regulations

Be specific and reference actual CFR sections and DATES when possible. 7-9 sentences."""

            response = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "gpt-4o-mini",
                    "messages": [
                        {"role": "system", "content": "You are a regulatory compliance expert who analyzes federal regulations for burden and complexity patterns."},
                        {"role": "user", "content": prompt}
                    ],
                    "temperature": 0.7,
                    "max_tokens": 800
                }
            )

            if response.status_code == 200:
                result = response.json()
                ai_analysis = result['choices'][0]['message']['content'].strip()
            else:
                ai_analysis = "AI analysis unavailable"

            return {
                'agency_name': agency.get('name'),
                'agency_slug': agency_slug,
                'cfr_references': [f"Title {r.get('title')} Chapter {r.get('chapter')}" for r in agency.get('cfr_references', [])],
                'sections_analyzed': len(regulation_samples),
                'signal_categories': signal_categories,
                'ai_analysis': ai_analysis,
                'sample_sections': regulation_samples[:5],
                'revision_history': sorted(revision_history, key=lambda x: x['date'], reverse=True),
                'recent_revisions_count': len(revision_history)
            }

        except httpx.HTTPError as e:
            raise HTTPException(status_code=502, detail=f"Error fetching regulation data: {str(e)}")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error performing analysis: {str(e)}")


@app.get("/api/deregulation/likelihood/{agency_slug}")
async def get_deregulation_likelihood(
    agency_slug: str,
    use_cache: bool = True,
    db: Session = Depends(database.get_db)
):
    """Get deregulation likelihood - uses cached results if available, falls back to live computation.

    Args:
        agency_slug: Agency slug identifier
        use_cache: If True (default), uses cached results. Set to False to force recomputation.
        db: Database session
    """
    # Try to get from cache first
    if use_cache:
        cached = database.get_deregulation_cache(db, agency_slug)
        if cached:
            print(f"✅ Cache hit for {agency_slug}")

            # Get last revision date from deep analysis
            last_revision_date = None
            try:
                deep_analysis = await get_deep_deregulation_analysis(agency_slug, db)
                if deep_analysis and deep_analysis.get('revision_history'):
                    # Get the most recent revision date
                    sorted_revisions = sorted(deep_analysis['revision_history'],
                                            key=lambda x: x['date'], reverse=True)
                    if sorted_revisions:
                        last_revision_date = sorted_revisions[0]['date']
            except:
                pass  # If we can't get revision history, just use None

            return {
                'likelihood': cached.likelihood,
                'label': cached.label,
                'recent_revisions': cached.recent_revisions,
                'analysis': cached.analysis,
                'full_analysis': cached.full_analysis,
                'word_count_change': None,
                'word_count_pct_change': None,
                'cached': True,
                'computed_at': cached.computed_at.isoformat(),
                'last_revision_date': last_revision_date
            }
        else:
            print(f"⚠️  Cache miss for {agency_slug} - will compute and cache")

    # Fall back to live computation if not cached or use_cache=False
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return {
            'likelihood': 'unknown',
            'label': 'Unknown',
            'recent_revisions': 0,
            'analysis': 'AI service not configured',
            'cached': False
        }

    try:
        # Get revision history for classification (lightweight - doesn't need full deep analysis)
        async with httpx.AsyncClient(timeout=120.0) as client:
            # Fetch agency data
            agencies_resp = await client.get("https://www.ecfr.gov/api/admin/v1/agencies.json")
            if agencies_resp.status_code != 200:
                raise HTTPException(status_code=502, detail="eCFR API error")

            agencies_list = agencies_resp.json()['agencies']
            agency = next((a for a in agencies_list if a.get('slug') == agency_slug), None)

            if not agency or not agency.get('cfr_references'):
                return {
                    'likelihood': 'unknown',
                    'label': 'Unknown',
                    'recent_revisions': 0,
                    'analysis': 'No CFR references found',
                    'last_revision_date': None
                }

            # Fetch revision history (last 12 months)
            from datetime import datetime, timedelta
            one_year_ago = datetime.now() - timedelta(days=365)

            unique_revision_dates = set()  # Track unique dates across all titles
            all_dates = []  # Keep all dates for finding the most recent

            for ref in agency.get('cfr_references', [])[:2]:  # Limit to first 2 for speed
                title = ref.get('title')
                if not title:
                    continue

                # Get versioner info - correct URL format
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
                                    from datetime import datetime as dt
                                    version_date = dt.strptime(issue_date, '%Y-%m-%d')
                                    if version_date >= one_year_ago:
                                        unique_revision_dates.add(issue_date)
                                        all_dates.append(issue_date)
                                except:
                                    pass
                except:
                    pass  # Skip if versioner API fails

            recent_revisions_count = len(unique_revision_dates)

            # Get last revision date
            last_revision_date = None
            if all_dates:
                last_revision_date = max(all_dates)

            # Use AI to analyze if revisions are deregulatory or administrative
            if recent_revisions_count == 0:
                likelihood, label = 'unlikely', 'Deregulation Unlikely'
                explanation = "No revisions in last 12 months"
                ai_analysis = None
            else:
                # Get sample of recent revision dates for context
                sorted_dates = sorted(unique_revision_dates, reverse=True)[:10]

                prompt = f"""Analyze the deregulation likelihood for {agency.get('name', agency_slug)}.

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
                    from openai import OpenAI

                    openai_client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

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
                    ai_analysis = None

            # Save to cache for future requests
            try:
                database.save_deregulation_cache(
                    db,
                    agency_slug=agency_slug,
                    agency_name=agency.get('name', agency_slug),
                    likelihood=likelihood,
                    label=label,
                    recent_revisions=recent_revisions_count,
                    analysis=explanation,
                    full_analysis=ai_analysis
                )
                print(f"💾 Saved to cache: {agency_slug} - {label}")
            except Exception as cache_error:
                print(f"Warning: Could not save to cache: {cache_error}")

        return {
            'likelihood': likelihood,
            'label': label,
            'recent_revisions': recent_revisions_count,
            'analysis': explanation,
            'full_analysis': ai_analysis,
            'word_count_change': None,
            'word_count_pct_change': None,
            'cached': False,  # Live computation
            'last_revision_date': last_revision_date
        }

    except Exception as e:
        print(f"Error getting likelihood for {agency_slug}: {e}")
        return {
            'likelihood': 'unknown',
            'label': 'Unknown',
            'recent_revisions': 0,
            'analysis': f'Error: {str(e)}',
            'cached': False
        }


@app.get("/api/rankings/word_count")
async def get_word_count_rankings(db: Session = Depends(database.get_db)):
    """Get agencies ranked by word count (latest snapshot)."""
    latest_date = database.get_latest_snapshot(db)

    if not latest_date:
        raise HTTPException(status_code=404, detail="No snapshots found")

    agencies = database.get_agencies_by_snapshot(db, latest_date)
    ranked = sorted(agencies, key=lambda a: a.word_count, reverse=True)

    return {
        'snapshot_date': latest_date.isoformat(),
        'rankings': [
            {
                'rank': idx + 1,
                'agency_name': a.agency_name,
                'word_count': a.word_count,
                'checksum': a.checksum
            }
            for idx, a in enumerate(ranked)
        ]
    }


@app.get("/api/rankings/complexity")
async def get_complexity_rankings(db: Session = Depends(database.get_db)):
    """Get agencies ranked by complexity score (latest snapshot)."""
    latest_date = database.get_latest_snapshot(db)

    if not latest_date:
        raise HTTPException(status_code=404, detail="No snapshots found")

    agencies = database.get_agencies_by_snapshot(db, latest_date)
    ranked = sorted(agencies, key=lambda a: a.complexity_score, reverse=True)

    return {
        'snapshot_date': latest_date.isoformat(),
        'rankings': [
            {
                'rank': idx + 1,
                'agency_name': a.agency_name,
                'complexity_score': a.complexity_score,
                'word_count': a.word_count
            }
            for idx, a in enumerate(ranked)
        ]
    }


@app.get("/api/trends/titles")
async def get_title_trends():
    """Get government-wide trends by CFR title for complexity and deregulation."""
    # Check cache first
    import time
    if TRENDS_CACHE["data"] and TRENDS_CACHE["timestamp"]:
        age = time.time() - TRENDS_CACHE["timestamp"]
        if age < CACHE_TTL:
            print(f"📦 Returning cached trends (age: {int(age)}s)")
            return TRENDS_CACHE["data"]

    print("🔄 Fetching fresh trends...")
    import httpx
    from datetime import datetime, timedelta
    from collections import defaultdict

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            # Get all CFR titles (1-50)
            all_titles = list(range(1, 51))

            # Generate last 12 months for frequency mode
            months_12 = []
            current_date = datetime.now()
            for i in range(11, -1, -1):
                month_date = current_date - timedelta(days=i*30)
                months_12.append(month_date.strftime('%b %y'))

            # Generate last 5 years for cumulative mode
            years_5 = []
            for i in range(4, -1, -1):
                year_date = current_date - timedelta(days=i*365)
                years_5.append(str(year_date.year))

            # Initialize data structures
            frequency_trends = defaultdict(lambda: {'revisions': []})  # Monthly revisions (12 months)
            cumulative_trends = defaultdict(lambda: {'revisions': []})  # Yearly cumulative (5 years)

            # For each title, fetch versioner data to get revision history
            for title_num in all_titles:  # Fetch all 50 CFR titles
                try:
                    versioner_url = f"https://www.ecfr.gov/api/versioner/v1/versions/title-{title_num}.json"
                    versioner_resp = await client.get(versioner_url, timeout=30.0)

                    if versioner_resp.status_code == 200:
                        versioner_data = versioner_resp.json()
                        versions = versioner_data.get('content_versions', [])

                        # Count monthly revisions (last 12 months)
                        monthly_revisions = [0] * 12
                        one_year_ago = current_date - timedelta(days=365)

                        for version in versions:
                            issue_date_str = version.get('issue_date')
                            if issue_date_str:
                                try:
                                    issue_date = datetime.strptime(issue_date_str, '%Y-%m-%d')
                                    if issue_date >= one_year_ago:
                                        months_ago = int((current_date - issue_date).days / 30)
                                        if 0 <= months_ago < 12:
                                            monthly_revisions[11 - months_ago] += 1
                                except:
                                    pass

                        frequency_trends[str(title_num)]['revisions'] = monthly_revisions

                        # Count yearly revisions (last 5 years)
                        yearly_revisions = [0] * 5
                        five_years_ago = current_date - timedelta(days=5*365)

                        for version in versions:
                            issue_date_str = version.get('issue_date')
                            if issue_date_str:
                                try:
                                    issue_date = datetime.strptime(issue_date_str, '%Y-%m-%d')
                                    if issue_date >= five_years_ago:
                                        years_ago = (current_date.year - issue_date.year)
                                        if 0 <= years_ago < 5:
                                            yearly_revisions[4 - years_ago] += 1
                                except:
                                    pass

                        cumulative_trends[str(title_num)]['revisions'] = yearly_revisions

                except Exception as e:
                    print(f"Error fetching title {title_num}: {e}")
                    continue

            result = {
                'months': months_12,  # For frequency mode
                'years': years_5,     # For cumulative mode
                'frequency_trends': dict(frequency_trends),
                'cumulative_trends': dict(cumulative_trends)
            }

            # Cache the result
            import time
            TRENDS_CACHE["data"] = result
            TRENDS_CACHE["timestamp"] = time.time()
            print(f"✅ Trends cached")

            return result

    except Exception as e:
        print(f"Error in get_title_trends: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")
