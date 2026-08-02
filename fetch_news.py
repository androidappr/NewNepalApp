import os
import re
import sys
from datetime import datetime, timezone
import feedparser
import requests
from dateutil import parser
from supabase import create_client, Client

# Initialize Supabase Client
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("Missing SUPABASE_URL or SUPABASE_SERVICE_KEY environment variables.")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# List of Nepali RSS feeds mapped with clean source names
RSS_FEEDS = [
    {"name": "Onlinekhabar", "url": "https://www.onlinekhabar.com/feed"},
    {"name": "Sidhakura", "url": "https://www.sidhakura.com/feed"},
    {"name": "Artha Sarokar", "url": "https://arthasarokar.com/feed"},
    {"name": "TechPana", "url": "https://techpana.com/feed"},
    {"name": "Nagarik News", "url": "https://nagariknews.nagariknetwork.com/feed"},
    {"name": "Setopati", "url": "https://www.setopati.com/feed"},
    {"name": "Annapurna Post", "url": "https://annapurnapost.com/rss/"},
    {"name": "BBC Nepali", "url": "https://feeds.bbci.co.uk/nepali/rss.xml"},
    {"name": "Shilapatra", "url": "https://shilapatra.com/feed"},
    {"name": "News 24 Nepal", "url": "https://www.news24nepal.com/feed"},
    {"name": "Ujyaalo Online", "url": "https://ujyaaloonline.com/feed/"},
    {"name": "Ratopati", "url": "https://www.ratopati.com/feed"},
    {"name": "Swasthya Khabar", "url": "https://swasthyakhabar.com/feed"},
    {"name": "Baahrakhari", "url": "https://baahrakhari.com/feed"},
    {"name": "Gorkhapatra", "url": "https://gorkhapatraonline.com/rss"},
    {"name": "Thaha Khabar", "url": "https://www.thahakhabar.com/feed"},
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}

def parse_date(date_string):
    """Converts RSS dates into PostgreSQL-compatible ISO timestamps."""
    if not date_string:
        return datetime.now(timezone.utc).isoformat()
    try:
        dt = parser.parse(date_string)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except Exception:
        return datetime.now(timezone.utc).isoformat()

def extract_image(entry, description):
    """Extracts article thumbnail from media tags, enclosures, or HTML body."""
    # 1. Media Content
    if 'media_content' in entry and len(entry.media_content) > 0:
        for media in entry.media_content:
            if media.get('url'):
                return media.get('url')

    # 2. Media Thumbnail
    if 'media_thumbnail' in entry and len(entry.media_thumbnail) > 0:
        return entry.media_thumbnail[0].get('url')

    # 3. Enclosures
    if 'enclosures' in entry and len(entry.enclosures) > 0:
        for enc in entry.enclosures:
            if enc.get('type', '').startswith('image/'):
                return enc.get('href')

    # 4. Fallback: Parse <img> tag src from HTML description
    if description:
        img_match = re.search(r'<img[^>]+src=["\'](.*?)["\']', description, re.IGNORECASE)
        if img_match:
            return img_match.group(1)

    return None

def fetch_and_store_news():
    all_news_items = []
    
    for feed in RSS_FEEDS:
        print(f"Fetching: {feed['name']}...")
        try:
            response = requests.get(feed['url'], headers=HEADERS, timeout=10)
            if response.status_code != 200:
                print(f"  Skipped (HTTP Status: {response.status_code})")
                continue

            parsed_feed = feedparser.parse(response.content)

            count = 0
            for entry in parsed_feed.entries[:15]:
                link = entry.get('link')
                title = entry.get('title')

                if not link or not title:
                    continue

                description = entry.get('summary', entry.get('description', ''))
                pub_date = parse_date(entry.get('published', entry.get('updated', '')))
                image_url = extract_image(entry, description)

                all_news_items.append({
                    "link": link.strip(),
                    "title": title.strip(),
                    "description": description.strip(),
                    "pub_date": pub_date,
                    "image_url": image_url,
                    "source_name": feed['name']
                })
                count += 1

            print(f"  Fetched {count} items")

        except Exception as e:
            print(f"  Error fetching {feed['name']}: {e}")

    if all_news_items:
        print(f"\nUpserting {len(all_news_items)} total items into Supabase...")
        batch_size = 50
        for i in range(0, len(all_news_items), batch_size):
            batch = all_news_items[i:i + batch_size]
            # Uncaught database exceptions will rightly trigger workflow step failures
            supabase.table("news").upsert(batch, on_conflict="link").execute()
        print("Successfully updated database!")
    else:
        print("No items fetched from any feed.")

if __name__ == "__main__":
    fetch_and_store_news()
