import os
import re
import sys
import logging
from datetime import datetime, timezone
import feedparser
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from dateutil import parser
from supabase import create_client, Client

# Configure Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    logging.error("Missing SUPABASE_URL or SUPABASE_SERVICE_KEY environment variables.")
    sys.exit(1)

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

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

def get_resilient_session():
    """Configures a request session with retries and realistic browser headers."""
    session = requests.Session()
    retries = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
    session.mount("https://", HTTPAdapter(max_retries=retries))
    session.mount("http://", HTTPAdapter(max_retries=retries))
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
        "Accept-Language": "en-US,en;q=0.9,ne;q=0.8",
        "Cache-Control": "no-cache",
    })
    return session

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

def clean_html(text):
    """Strips HTML tags from text content."""
    if not text:
        return ""
    clean = re.sub(r'<[^>]+>', '', text)
    return clean.strip()

def extract_image(entry, raw_description):
    """Extracts thumbnail images safely."""
    if 'media_content' in entry and entry.media_content:
        for media in entry.media_content:
            if media.get('url'):
                return media.get('url')

    if 'media_thumbnail' in entry and entry.media_thumbnail:
        return entry.media_thumbnail[0].get('url')

    if 'enclosures' in entry and entry.enclosures:
        for enc in entry.enclosures:
            if enc.get('type', '').startswith('image/'):
                return enc.get('href')

    if raw_description:
        img_match = re.search(r'<img[^>]+src=["\'](.*?)["\']', raw_description, re.IGNORECASE)
        if img_match:
            return img_match.group(1)

    return None

def fetch_and_store_news():
    session = get_resilient_session()
    all_news_items = []

    for feed in RSS_FEEDS:
        logging.info(f"Fetching feed: {feed['name']}")
        try:
            response = session.get(feed['url'], timeout=12)
            if response.status_code != 200:
                logging.warning(f"Skipped {feed['name']} (HTTP Status: {response.status_code})")
                continue

            parsed_feed = feedparser.parse(response.content)
            count = 0

            for entry in parsed_feed.entries[:15]:
                link = entry.get('link')
                title = entry.get('title')

                if not link or not title:
                    continue

                raw_description = entry.get('summary', entry.get('description', ''))
                pub_date = parse_date(entry.get('published', entry.get('updated', '')))
                image_url = extract_image(entry, raw_description)
                clean_desc = clean_html(raw_description)

                all_news_items.append({
                    "link": link.strip(),
                    "title": title.strip(),
                    "description": clean_desc,
                    "pub_date": pub_date,
                    "image_url": image_url,
                    "source_name": feed['name']
                })
                count += 1

            logging.info(f"Successfully processed {count} items from {feed['name']}")

        except Exception as e:
            logging.error(f"Failed to fetch {feed['name']}: {e}")

    if all_news_items:
        logging.info(f"Upserting {len(all_news_items)} total items into Supabase...")
        batch_size = 50
        for i in range(0, len(all_news_items), batch_size):
            batch = all_news_items[i:i + batch_size]
            try:
                supabase.table("news").upsert(batch, on_conflict="link").execute()
            except Exception as db_err:
                logging.error(f"Database batch upsert failed at index {i}: {db_err}")
        logging.info("Database sync finished.")
    else:
        logging.warning("No news items retrieved from any source.")

if __name__ == "__main__":
    fetch_and_store_news()
