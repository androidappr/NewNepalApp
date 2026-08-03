import os
import re
import json
import html
import logging
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin, urlparse
from concurrent.futures import ThreadPoolExecutor
import feedparser
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from dateutil import parser

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

OUTPUT_DIR = "public"
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "news.json")
MAX_NEWS_ITEMS = 200

NEPAL_TZ = timezone(timedelta(hours=5, minutes=45))

EXCLUDED_CATEGORIES = {"Breaking News", "Popular News"}

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

def extract_domain_name(url):
    if not url:
        return ""
    try:
        netloc = urlparse(url).netloc.split(':')[0].lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        return netloc.upper()
    except Exception:
        return ""

def get_resilient_session():
    session = requests.Session()
    retries = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
    session.mount("https://", HTTPAdapter(max_retries=retries))
    session.mount("http://", HTTPAdapter(max_retries=retries))
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,ne;q=0.8",
        "Cache-Control": "no-cache",
    })
    return session

def parse_date(date_string):
    if not date_string:
        return None
    try:
        dt = parser.parse(str(date_string))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=NEPAL_TZ)
        else:
            dt = dt.astimezone(NEPAL_TZ)
        return dt.isoformat()
    except Exception:
        return None

def extract_entry_date(entry):
    for field in ['published', 'updated', 'created', 'pubDate', 'dc_date', 'date', 'post_date']:
        val = entry.get(field)
        if val:
            parsed = parse_date(val)
            if parsed:
                return parsed

    for parsed_field in ['published_parsed', 'updated_parsed', 'created_parsed']:
        tp = entry.get(parsed_field)
        if tp:
            try:
                dt = datetime(*tp[:6], tzinfo=timezone.utc).astimezone(NEPAL_TZ)
                return dt.isoformat()
            except Exception:
                pass
    return None

def clean_html(text):
    if not text:
        return ""
    clean = re.sub(r'<[^>]+>', '', text)
    return html.unescape(clean).strip()

def extract_image_from_text(text, base_url):
    if not text:
        return None
    text = html.unescape(text)
    img_match = re.search(r'<img[^>]+src=["\'](.*?)["\']', text, re.IGNORECASE)
    if img_match:
        img_src = img_match.group(1).strip()
        if img_src and not img_src.startswith("data:"):
            return urljoin(base_url, img_src)
    return None

def extract_image_from_entry(entry, base_url):
    if 'media_content' in entry and entry.media_content:
        for media in entry.media_content:
            if isinstance(media, dict) and media.get('url'):
                return urljoin(base_url, media.get('url'))

    if 'media_thumbnail' in entry and entry.media_thumbnail:
        for thumb in entry.media_thumbnail:
            if isinstance(thumb, dict) and thumb.get('url'):
                return urljoin(base_url, thumb.get('url'))

    if 'enclosures' in entry and entry.enclosures:
        for enc in entry.enclosures:
            if enc.get('type', '').startswith('image/') and enc.get('href'):
                return urljoin(base_url, enc.get('href'))

    if 'image' in entry and isinstance(entry.image, dict) and entry.image.get('href'):
        return urljoin(base_url, entry.image.get('href'))

    for field in ['content', 'summary_detail', 'description_detail']:
        if field in entry:
            val = entry[field]
            if isinstance(val, list):
                for v in val:
                    if isinstance(v, dict) and v.get('value'):
                        img = extract_image_from_text(v.get('value'), base_url)
                        if img:
                            return img
            elif isinstance(val, dict) and val.get('value'):
                img = extract_image_from_text(val.get('value'), base_url)
                if img:
                    return img

    for field in ['summary', 'description', 'story']:
        if field in entry and entry[field]:
            img = extract_image_from_text(entry[field], base_url)
            if img:
                return img

    return None

def fetch_article_metadata(session, url):
    img_url = None
    pub_date = None
    if not url:
        return img_url, pub_date
    try:
        resp = session.get(url, timeout=8, allow_redirects=True)
        if resp.status_code == 200:
            text = resp.text

            img_patterns = [
                r'<meta[^>]+property=["\']og:image["\']\s+content=["\'](.*?)["\']',
                r'<meta[^>]+content=["\'](.*?)["\']\s+property=["\']og:image["\']',
                r'<meta[^>]+name=["\']twitter:image["\']\s+content=["\'](.*?)["\']',
                r'<meta[^>]+content=["\'](.*?)["\']\s+name=["\']twitter:image["\']',
                r'<meta[^>]+property=["\']twitter:image["\']\s+content=["\'](.*?)["\']',
                r'<link[^>]+rel=["\']image_src["\']\s+href=["\'](.*?)["\']',
            ]
            for pat in img_patterns:
                m = re.search(pat, text, re.IGNORECASE)
                if m:
                    found_img = html.unescape(m.group(1).strip())
                    if found_img and not found_img.startswith('data:'):
                        img_url = urljoin(url, found_img)
                        break

            date_patterns = [
                r'<meta[^>]+property=["\']article:published_time["\']\s+content=["\'](.*?)["\']',
                r'<meta[^>]+content=["\'](.*?)["\']\s+property=["\']article:published_time["\']',
                r'<meta[^>]+name=["\']pubdate["\']\s+content=["\'](.*?)["\']',
                r'<meta[^>]+content=["\'](.*?)["\']\s+name=["\']pubdate["\']',
                r'<meta[^>]+name=["\']publishdate["\']\s+content=["\'](.*?)["\']',
                r'<meta[^>]+content=["\'](.*?)["\']\s+name=["\']publishdate["\']',
                r'<meta[^>]+property=["\']og:published_time["\']\s+content=["\'](.*?)["\']',
                r'"datePublished"\s*:\s*"([^"]+)"',
                r'"dateCreated"\s*:\s*"([^"]+)"',
                r'"published_at"\s*:\s*"([^"]+)"',
                r'"created_at"\s*:\s*"([^"]+)"',
                r'"published_date"\s*:\s*"([^"]+)"',
                r'"publish_date"\s*:\s*"([^"]+)"',
            ]
            for pat in date_patterns:
                m = re.search(pat, text, re.IGNORECASE)
                if m:
                    raw_d = html.unescape(m.group(1).strip())
                    parsed_d = parse_date(raw_d)
                    if parsed_d:
                        pub_date = parsed_d
                        break
    except Exception:
        pass
    return img_url, pub_date

def safe_parse_dt(iso_str):
    if not iso_str:
        return datetime.min.replace(tzinfo=NEPAL_TZ)
    try:
        dt = parser.parse(iso_str)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=NEPAL_TZ)
        return dt.astimezone(NEPAL_TZ)
    except Exception:
        return datetime.min.replace(tzinfo=NEPAL_TZ)

def determine_categories(entry, title, link, clean_desc, source_name):
    feed_cats = []
    if 'tags' in entry:
        for t in entry.tags:
            if isinstance(t, dict) and 'term' in t:
                feed_cats.append(str(t['term']).lower())
            elif hasattr(t, 'term'):
                feed_cats.append(str(t.term).lower())
    if 'category' in entry and entry.category:
        feed_cats.append(str(entry.category).lower())

    feed_cat_str = " ".join(feed_cats)
    link_lower = link.lower()
    title_lower = title.lower()
    desc_lower = clean_desc.lower()
    full_text = f"{feed_cat_str} {link_lower} {title_lower} {desc_lower}"

    categories = set()

    if "swasthyakhabar" in source_name.lower():
        categories.add("Health News")
    if "techpana" in source_name.lower():
        categories.add("Technology News")

    if any(k in full_text for k in ['share-market', 'sharemarket', 'nepse', 'शेयर', 'सेयर', 'नेप्से', 'लाभांश', 'आइपिओ', 'ipo', 'म्युचुअल फन्ड', 'राइट सेयर', 'share market', 'share-bazar']):
        categories.add("Share Market News")

    if any(k in full_text for k in ['sports', 'khelkud', 'खेलकुद', 'क्रिकेट', 'फुटबल', 'मेस्सी', 'रोनाल्डो', 'क्यान', 'आइपिएल', 'ipl', 'cricket', 'football', 'साफ', 'ओलम्पिक', 'खेल']):
        categories.add("Sports News")

    if any(k in full_text for k in ['entertainment', 'manoranjan', 'मनोरञ्जन', 'कला', 'सिनेमा', 'फिल्म', 'नायक', 'नायिका', 'मोडल', 'हलिउड', 'बलिवुड', 'कलिउड', 'movie', 'cinema', 'गीत', 'संगीत', 'अभिनेता', 'अभिनेत्री']):
        categories.add("Entertainment News")

    if any(k in full_text for k in ['health', 'swasthya', 'स्वास्थ्य', 'कोरोना', 'अस्पताल', 'चिकित्सा', 'डाक्टर', 'औषधि', 'रोग', 'संक्रमण']):
        categories.add("Health News")

    if any(k in full_text for k in ['tech', 'technology', 'prabidhi', 'प्रविधि', 'टेक', 'आइटी', 'सफ्टवेयर', 'इन्टरनेट', 'डिजिटल', 'ai', 'स्मार्टफोन', 'साइबर', 'gadget']):
        categories.add("Technology News")

    if any(k in full_text for k in ['politics', 'rajneeti', 'राजनीति', 'नेता', 'पार्टी', 'निर्वाचन', 'चुनाव', 'संसद', 'मन्त्री', 'प्रधानमन्त्री', 'सरकार', 'सांसद', 'कांग्रेस', 'एमाले', 'माओवादी', 'रास्वपा', 'प्रतिनिधिसभा', 'प्रदेशसभा']):
        categories.add("Political News")

    if any(k in full_text for k in ['economy', 'economic', 'arthik', 'आर्थिक', 'अर्थतन्त्र', 'बजेट', 'राजस्व', 'मौद्रिक', 'अर्थशास्त्र', 'मुद्रास्फीति']):
        categories.add("Economic News")

    if any(k in full_text for k in ['business', 'wyapar', 'व्यापार', 'वाणिज्य', 'उद्योग', 'व्यापारी', 'कर्पोरेट', 'उद्योगी', 'वाणिज्य बैंक', 'वित्तीय']):
        categories.add("Business News")

    intl_keywords = [
        'international', 'videsh', 'bidesh', 'world', 'global', 'foreign',
        'विश्व', 'विदेश', 'अन्तर्राष्ट्रिय', 'परराष्ट्र',
        'अमेरिका', 'चीन', 'भारत', 'रुस', 'युक्रेन', 'इन्डोनेसिया', 'जापान', 'कोरिया',
        'बेलायत', 'अस्ट्रेलिया', 'क्यानडा', 'इजरायल', 'गाजा', 'प्यालेस्टाइन', 'पाकिस्तान',
        'बंगलादेश', 'श्रीलंका', 'इरान', 'इराक', 'टर्की', 'सउदी', 'कतार', 'युएई'
    ]
    intl_url_slugs = ['/world/', '/international/', '/bidesh/', '/videsh/']

    if any(k in full_text for k in intl_keywords) or any(slug in link_lower for slug in intl_url_slugs):
        categories.add("International News")

    if not categories:
        categories.add("National News")

    return sorted(list(categories))

def fetch_and_store_news():
    session = get_resilient_session()
    raw_entries = []

    for feed in RSS_FEEDS:
        logging.info(f"Fetching feed: {feed['name']}")
        try:
            response = session.get(feed['url'], timeout=12)
            if response.status_code != 200:
                logging.warning(f"Skipped {feed['name']} (HTTP Status: {response.status_code})")
                continue

            parsed_feed = feedparser.parse(response.content)

            for entry in parsed_feed.entries[:15]:
                link = entry.get('link')
                title = entry.get('title')

                if not link or not title:
                    continue

                link = link.strip()
                title = title.strip()
                raw_description = entry.get('summary', entry.get('description', ''))
                clean_desc = clean_html(raw_description)

                if len(clean_desc.split()) < 10:
                    continue

                pub_date = extract_entry_date(entry)
                image_url = extract_image_from_entry(entry, link)
                source_domain = extract_domain_name(link) or extract_domain_name(feed['url'])

                raw_entries.append({
                    "entry": entry,
                    "link": link,
                    "title": title,
                    "description": clean_desc,
                    "pub_date": pub_date,
                    "image_url": image_url,
                    "source_name": source_domain
                })

        except Exception as e:
            logging.error(f"Failed to fetch {feed['name']}: {e}")

    missing_meta_items = [item for item in raw_entries if not item["image_url"] or not item["pub_date"]]
    if missing_meta_items:
        logging.info(f"Scraping webpage metadata for {len(missing_meta_items)} items...")
        def scrape_item(item):
            img, d = fetch_article_metadata(session, item["link"])
            if not item["image_url"] and img:
                item["image_url"] = img
            if not item["pub_date"] and d:
                item["pub_date"] = d

        with ThreadPoolExecutor(max_workers=10) as executor:
            executor.map(scrape_item, missing_meta_items)

    fetched_items = []

    for item in raw_entries:
        categories = determine_categories(
            item['entry'], 
            item['title'], 
            item['link'], 
            item['description'], 
            item['source_name']
        )
        fetched_items.append({
            "link": item['link'],
            "title": item['title'],
            "description": item['description'],
            "categories": categories,
            "pub_date": item['pub_date'],
            "image_url": item['image_url'],
            "source_name": item['source_name']
        })

    existing_items = []
    existing_map = {}
    if os.path.exists(OUTPUT_FILE):
        try:
            with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
                existing_items = json.load(f)
                for ex in existing_items:
                    if ex.get("link"):
                        existing_map[ex["link"]] = ex
        except Exception as err:
            logging.warning(f"Could not read existing news file: {err}")

    seen_links = set()
    combined_items = []

    for item in fetched_items:
        link = item.get("link")
        desc = item.get("description", "")

        if len(desc.split()) < 10 or not link or link in seen_links:
            continue

        seen_links.add(link)

        if link in existing_map:
            ex_date = existing_map[link].get("pub_date")
            if ex_date and not item.get("pub_date"):
                item["pub_date"] = ex_date

        combined_items.append(item)

    for ex in existing_items:
        link = ex.get("link")
        desc = ex.get("description", "")
        if link and link not in seen_links and len(desc.split()) >= 10:
            seen_links.add(link)
            
            ex["source_name"] = extract_domain_name(link)

            if "category" in ex and "categories" not in ex:
                cat_val = ex.pop("category")
                ex["categories"] = cat_val if isinstance(cat_val, list) else [cat_val]
            
            if "categories" in ex:
                ex["categories"] = [c for c in ex["categories"] if c not in EXCLUDED_CATEGORIES]
                if not ex["categories"]:
                    ex["categories"] = ["National News"]

            combined_items.append(ex)

    combined_items.sort(key=lambda x: safe_parse_dt(x.get("pub_date")), reverse=True)

    final_news = combined_items[:MAX_NEWS_ITEMS]

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(final_news, f, ensure_ascii=False, indent=2)

    logging.info(f"Successfully generated {OUTPUT_FILE} with {len(final_news)} news items.")

if __name__ == "__main__":
    fetch_and_store_news()
