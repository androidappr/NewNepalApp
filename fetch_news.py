import os
import re
import json
import logging
from datetime import datetime, timezone
import feedparser
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from dateutil import parser

# Configure Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

OUTPUT_DIR = "public"
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "news.json")
MAX_NEWS_ITEMS = 200  # Keeps file lightweight for mobile app

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
    """Converts RSS dates into standard ISO timestamps."""
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

def safe_parse_dt(iso_str):
    """Safely converts ISO string to datetime for sorting."""
    try:
        return parser.parse(iso_str)
    except Exception:
        return datetime.min.replace(tzinfo=timezone.utc)

def fetch_and_store_news():
    session = get_resilient_session()
    fetched_items = []

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

                fetched_items.append({
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

    # Read existing news.json if available to prevent dropping older articles
    existing_items = []
    if os.path.exists(OUTPUT_FILE):
        try:
            with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
                existing_items = json.load(f)
        except Exception as err:
            logging.warning(f"Could not read existing news file: {err}")

    # Merge newly fetched and existing news, deduplicating by 'link'
    seen_links = set()
    combined_items = []

    # Process fetched items first, then existing
    for item in fetched_items + existing_items:
        link = item.get("link")
        if link and link not in seen_links:
            seen_links.add(link)
            combined_items.append(item)

    # Sort items by date (newest first)
    combined_items.sort(key=lambda x: safe_parse_dt(x.get("pub_date", "")), reverse=True)

    # Keep only the latest MAX_NEWS_ITEMS articles
    final_news = combined_items[:MAX_NEWS_ITEMS]

    # Ensure output directory exists
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Save to public/news.json
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(final_news, f, ensure_ascii=False, indent=2)

    logging.info(f"Successfully generated {OUTPUT_FILE} with {len(final_news)} news items.")

if __name__ == "__main__":
    fetch_and_store_news()
