"""eCFR data fetcher using public API."""
import asyncio
import hashlib
import re
from datetime import datetime
from typing import Dict, List
import httpx
from bs4 import BeautifulSoup


class ECFRFetcher:
    """Fetches and parses eCFR data from public API."""

    def __init__(self):
        self.base_url = "https://www.ecfr.gov/api"
        self.client = httpx.AsyncClient(timeout=300.0, follow_redirects=True)

    async def fetch_agencies(self) -> List[Dict]:
        """Fetch all agencies with CFR references."""
        url = f"{self.base_url}/admin/v1/agencies.json"
        response = await self.client.get(url)
        response.raise_for_status()
        data = response.json()
        return data.get("agencies", [])

    async def fetch_title_versions(self, title_num: int) -> List[Dict]:
        """Fetch version history for a title from eCFR API."""
        url = f"{self.base_url}/versioner/v1/versions/title-{title_num}.json"
        try:
            response = await self.client.get(url)
            response.raise_for_status()
            data = response.json()
            return data.get("content_versions", [])
        except:
            return []

    async def fetch_title_xml(self, title_num: int, date: str = "2025-01-01") -> str:
        """Fetch full XML content for a CFR title at a specific date."""
        url = f"{self.base_url}/versioner/v1/full/{date}/title-{title_num}.xml"
        response = await self.client.get(url)
        response.raise_for_status()
        return response.text

    def parse_title_xml(self, xml_content: str) -> Dict:
        """Parse XML to extract text and metadata."""
        soup = BeautifulSoup(xml_content, 'xml')
        full_text = soup.get_text(separator=' ', strip=True)

        # Extract chapters
        chapters = {}
        for chapter in soup.find_all(['CHAPTER', 'DIV3']):
            chapter_id = chapter.get('N', 'UNKNOWN')
            chapter_text = chapter.get_text(separator=' ', strip=True)
            chapters[chapter_id] = {
                'text': chapter_text,
                'word_count': len(chapter_text.split())
            }

        return {
            'text': full_text,
            'word_count': len(full_text.split()),
            'chapters': chapters,
            'checksum': hashlib.sha256(full_text.encode()).hexdigest()
        }

    def calculate_complexity_score(self, text: str) -> float:
        """Custom metric: Regulatory Complexity Score.

        Measures regulatory burden through:
        - Modal verbs (shall, must, may, should) indicating obligations
        - Cross-references (§, CFR) indicating interconnectedness
        - Legal terms (penalty, violation, compliance) indicating enforcement
        - Exception clauses (except, unless, provided) adding conditional logic

        Higher scores = more complex/burdensome regulations.
        """
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

    async def close(self):
        """Close HTTP client."""
        await self.client.aclose()


async def fetch_agency_data(max_agencies: int = 10) -> List[Dict]:
    """Fetch and process agency regulation data including parent/child relationships.

    Args:
        max_agencies: Limit number of agencies (for speed)

    Returns:
        List of agency data with metrics and parent/child info
    """
    fetcher = ECFRFetcher()

    try:
        print("Fetching agencies...")
        agencies = await fetcher.fetch_agencies()

        # Build parent-child relationships
        agency_map = {a.get('slug', ''): a for a in agencies if a.get('slug')}

        # Filter agencies with CFR references
        agencies_with_refs = [
            a for a in agencies
            if a.get("cfr_references") and len(a.get("cfr_references", [])) > 0
        ][:max_agencies]

        print(f"Processing {len(agencies_with_refs)} agencies (with parent/child relationships)...")

        # Track which titles we need
        titles_needed = set()
        for agency in agencies_with_refs:
            for ref in agency.get("cfr_references", []):
                if ref.get("title"):
                    titles_needed.add(ref["title"])

        # Fetch titles with rate limiting (avoid 429 errors)
        print(f"Fetching {len(titles_needed)} CFR titles...")
        title_data = {}

        async def fetch_title(title_num, delay=0):
            try:
                if delay > 0:
                    await asyncio.sleep(delay)
                print(f"  Fetching Title {title_num}...")
                xml = await fetcher.fetch_title_xml(title_num)
                parsed = fetcher.parse_title_xml(xml)
                print(f"  ✓ Title {title_num}: {parsed['word_count']:,} words")
                return title_num, parsed
            except Exception as e:
                print(f"  ✗ Error fetching title {title_num}: {e}")
                return title_num, None

        # Fetch sequentially with delays to avoid rate limits
        results = []
        for idx, title_num in enumerate(sorted(titles_needed)):
            delay = 2 if idx > 0 else 0  # 2 second delay between requests
            result = await fetch_title(title_num, delay)
            results.append(result)

        for title_num, data in results:
            if data:
                title_data[title_num] = data

        # Process agencies
        processed_agencies = []
        for agency in agencies_with_refs:
            agency_name = agency.get("name", "Unknown")

            # Aggregate data across all titles/chapters this agency regulates
            total_words = 0
            all_text = []
            checksums = []

            for ref in agency.get("cfr_references", []):
                title_num = ref.get("title")
                chapter_id = ref.get("chapter")

                if title_num in title_data:
                    td = title_data[title_num]

                    # Use specific chapter if available, otherwise full title
                    if chapter_id and chapter_id in td['chapters']:
                        chapter = td['chapters'][chapter_id]
                        total_words += chapter['word_count']
                        all_text.append(chapter['text'])
                    else:
                        total_words += td['word_count']
                        all_text.append(td['text'])

                    checksums.append(td['checksum'])

            # Calculate metrics
            combined_text = ' '.join(all_text)
            complexity = fetcher.calculate_complexity_score(combined_text)

            # Aggregate checksum
            aggregate_checksum = hashlib.sha256(
                ''.join(sorted(checksums)).encode()
            ).hexdigest()[:16]

            # Get parent and child agency information
            parent_slug = agency.get('parent_slug')
            parent_name = None
            if parent_slug and parent_slug in agency_map:
                parent_name = agency_map[parent_slug].get('name')

            # Find child agencies
            agency_slug = agency.get('slug', '')
            children = []
            if agency_slug:
                for other_agency in agencies:
                    if other_agency.get('parent_slug') == agency_slug:
                        children.append({
                            'name': other_agency.get('name'),
                            'slug': other_agency.get('slug')
                        })

            processed_agencies.append({
                'name': agency_name,
                'slug': agency.get('slug', ''),
                'parent_agency': parent_name,
                'child_agencies': children,
                'word_count': total_words,
                'checksum': aggregate_checksum,
                'complexity_score': complexity,
                'cfr_references': agency.get('cfr_references', [])
            })

        # Fetch historical snapshots by downloading XML for past dates
        print(f"\n📚 Fetching historical data from eCFR API...")
        historical_snapshots = []

        # Determine which titles we need historical data for
        titles_to_fetch = {}  # title_num -> [agencies using it]
        for agency in processed_agencies:
            for ref in agency.get('cfr_references', []):
                title_num = ref.get('title')
                if title_num:
                    if title_num not in titles_to_fetch:
                        titles_to_fetch[title_num] = []
                    titles_to_fetch[title_num].append(agency)

        # For each title, get version history and fetch historical XML
        from datetime import datetime as dt, timedelta

        for title_num, agencies_using_title in list(titles_to_fetch.items())[:3]:  # Limit to 3 titles for performance
            print(f"\n  Fetching historical data for Title {title_num}...")

            try:
                # Generate yearly snapshots from 2020 to now
                current_date = dt.utcnow()
                start_year = 2020

                historical_dates = []
                for year in range(start_year, current_date.year + 1):
                    # Fetch once per year on January 1st
                    snapshot_date = dt(year, 1, 1)
                    if snapshot_date <= current_date:
                        historical_dates.append(snapshot_date.strftime("%Y-%m-%d"))

                # Add current date as most recent
                historical_dates.append(current_date.strftime("%Y-%m-%d"))

                print(f"    Fetching {len(historical_dates)} yearly snapshots from {start_year} to present")

                # Fetch XML for each historical date
                for idx, date_str in enumerate(historical_dates):
                    try:
                        await asyncio.sleep(2)  # Rate limiting
                        print(f"    [{idx+1}/{len(historical_dates)}] Fetching Title {title_num} for {date_str}...")

                        xml = await fetcher.fetch_title_xml(title_num, date_str)
                        parsed = fetcher.parse_title_xml(xml)

                        # Create historical snapshot for each agency using this title
                        for agency in agencies_using_title:
                            # Calculate metrics for this historical version
                            total_words = 0
                            all_text = []

                            for ref in agency.get('cfr_references', []):
                                if ref.get('title') == title_num:
                                    chapter_id = ref.get('chapter')

                                    if chapter_id and chapter_id in parsed['chapters']:
                                        chapter = parsed['chapters'][chapter_id]
                                        total_words += chapter['word_count']
                                        all_text.append(chapter['text'])
                                    else:
                                        total_words += parsed['word_count']
                                        all_text.append(parsed['text'])

                            combined_text = ' '.join(all_text)
                            complexity = fetcher.calculate_complexity_score(combined_text)

                            historical_snapshots.append({
                                'name': agency['name'],
                                'slug': agency['slug'],
                                'parent_agency': agency.get('parent_agency'),
                                'child_agencies': agency.get('child_agencies', []),
                                'word_count': total_words,
                                'checksum': parsed['checksum'][:16],
                                'complexity_score': complexity,
                                'cfr_references': agency['cfr_references'],
                                'fetched_at': date_str
                            })

                        print(f"      ✓ Processed {date_str}")

                    except Exception as e:
                        print(f"      ✗ Error fetching {date_str}: {e}")
                        continue

            except Exception as e:
                print(f"  ✗ Error with Title {title_num}: {e}")
                continue

        print(f"\n  ✓ Created {len(historical_snapshots)} historical snapshots")

        # Combine current + historical
        all_snapshots = processed_agencies + historical_snapshots
        return all_snapshots

    finally:
        await fetcher.close()
