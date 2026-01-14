"""Pre-fetch all CFR word counts and cache to file."""
import asyncio
import json
from datetime import datetime
from bs4 import BeautifulSoup
import httpx


async def prefetch_all_word_counts():
    """Fetch all agencies and their word counts, save to cache file."""
    cache_file = "cfr_word_counts_cache.json"

    async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
        print("Fetching agencies from eCFR API...")
        response = await client.get("https://www.ecfr.gov/api/admin/v1/agencies.json")
        response.raise_for_status()
        data = response.json()

        agencies_list = data.get('agencies', [])
        print(f"Found {len(agencies_list)} agencies")

        # Collect all unique titles needed
        titles_needed = set()
        for agency in agencies_list:
            for ref in agency.get('cfr_references', []):
                if ref.get('title'):
                    titles_needed.add(ref['title'])
            for child in agency.get('children', []):
                for ref in child.get('cfr_references', []):
                    if ref.get('title'):
                        titles_needed.add(ref['title'])

        print(f"Need to fetch {len(titles_needed)} unique CFR titles")

        # Fetch all title XMLs and parse chapters
        title_cache = {}

        for idx, title_num in enumerate(sorted(titles_needed)):
            try:
                if idx > 0:
                    await asyncio.sleep(2)  # Rate limiting

                url = f"https://www.ecfr.gov/api/versioner/v1/full/2025-01-01/title-{title_num}.xml"
                print(f"[{idx+1}/{len(titles_needed)}] Fetching Title {title_num}...")

                response = await client.get(url, timeout=120.0)

                if response.status_code == 200:
                    soup = BeautifulSoup(response.text, 'xml')

                    # Get full title word count
                    full_text = soup.get_text(separator=' ', strip=True)
                    full_word_count = len(full_text.split())

                    # Extract chapters
                    chapters = {}
                    for chapter_elem in soup.find_all(['CHAPTER', 'DIV3']):
                        chapter_id = chapter_elem.get('N', 'UNKNOWN')
                        chapter_text = chapter_elem.get_text(separator=' ', strip=True)
                        chapters[chapter_id] = len(chapter_text.split())

                    title_cache[str(title_num)] = {
                        'word_count': full_word_count,
                        'chapters': chapters
                    }

                    print(f"  ✓ {full_word_count:,} words ({len(chapters)} chapters)")
                else:
                    print(f"  ✗ HTTP {response.status_code}")
                    title_cache[str(title_num)] = {'word_count': 0, 'chapters': {}}

            except Exception as e:
                print(f"  ✗ Error: {e}")
                title_cache[str(title_num)] = {'word_count': 0, 'chapters': {}}

        # Build the cache structure
        cache = {
            'fetched_at': datetime.utcnow().isoformat(),
            'agencies': agencies_list,
            'title_word_counts': title_cache
        }

        # Save to file
        print(f"\nSaving cache to {cache_file}...")
        with open(cache_file, 'w') as f:
            json.dump(cache, f, indent=2)

        print(f"✓ Cache saved! ({len(title_cache)} titles)")
        return cache


if __name__ == "__main__":
    print("Starting CFR word count pre-fetch...")
    print("This will take several minutes (fetching 50+ CFR titles)\n")
    asyncio.run(prefetch_all_word_counts())
