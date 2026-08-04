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

EXCLUDED_CATEGORIES = {"Popular News"}

NEP_TO_ENG_DIGITS = str.maketrans('०१२३४५६७८९', '0123456789')

NEP_STOP_WORDS = {
    "नेपाल", "नेपालका", "सरकार", "काठमाडौं", "काठमाडौँ", "पोखरा", "प्रहरी", "भने", 
    "भएका", "भइरहेको", "अनुसार", "बारे", "गरिएको", "गरेको", "गर्न", "भएको", "लागि",
    "nepal", "kathmandu", "news", "today", "update"
}

RSS_FEEDS = [
    {"name": "Onlinekhabar", "url": "https://www.onlinekhabar.com/feed"},
    {"name": "Sidhakura", "url": "https://www.sidhakura.com/feed"},
    {"name": "Artha Sarokar", "url": "https://arthasarokar.com/feed"},
    {"name": "TechPana", "url": "https://techpana.com/feed/"},
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
    {"name": "News of Nepal", "url": "https://newsofnepal.com/feed/"},
    {"name": "Kathmandu Post", "url": "https://kathmandupost.com/rss"},
    {"name": "Rajdhani Daily", "url": "https://rajdhanidaily.com/feed/"},
    {"name": "Lokpath", "url": "https://www.lokpath.com/feed/"},
    {"name": "Pahilopost", "url": "https://pahilopost.com/feed"},
    {"name": "Image Khabar", "url": "https://www.imagekhabar.com/feed/"},
    {"name": "Bizmandu", "url": "https://bizmandu.com/feed"},
    {"name": "Clickmandu", "url": "https://clickmandu.com/feed"},
    {"name": "Arthasansar", "url": "https://arthasansar.com/feed"},
    {"name": "DC Nepal", "url": "https://www.dcnepal.com/feed/"},
]

def normalize_digits(text):
    if not text:
        return text
    return str(text).translate(NEP_TO_ENG_DIGITS)

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
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,ne;q=0.8",
        "Referer": "https://www.google.com/",
        "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "cross-site",
        "Cache-Control": "max-age=0",
    })
    return session

def parse_date(date_string):
    if not date_string:
        return None
    try:
        clean_str = normalize_digits(str(date_string))
        dt = parser.parse(clean_str)
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
    clean = html.unescape(html.unescape(text))
    clean = re.sub(r'<[^>]+>', '', clean)
    return clean.strip()

def extract_image_from_text(text, base_url):
    if not text:
        return None
    text = html.unescape(html.unescape(text))
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

def check_keywords(full_text, keywords):
    if not full_text or not keywords:
        return False

    full_text_lower = full_text.lower()
    words = set(re.findall(r'[\u0900-\u097F\w]+', full_text_lower))

    for k in keywords:
        k_clean = k.strip().lower()
        if not k_clean:
            continue

        if ' ' in k_clean or '-' in k_clean:
            if k_clean in full_text_lower:
                return True
        else:
            if k_clean in words:
                return True

    return False

def get_explicit_category(entry, link, source_name):
    feed_tags = []
    if 'tags' in entry:
        for t in entry.tags:
            if isinstance(t, dict) and 'term' in t:
                feed_tags.append(str(t['term']).lower())
            elif hasattr(t, 'term'):
                feed_tags.append(str(t.term).lower())
    if 'category' in entry and entry.category:
        feed_tags.append(str(entry.category).lower())

    url_path = urlparse(link).path.lower()

    if "swasthyakhabar" in source_name.lower():
        return "Health News"
    if "techpana" in source_name.lower():
        return "Technology News"

    mappings = [
        ("Share Market News", ["/share/", "/stock/", "/nepse/", "share", "stock", "nepse", "ipo", "trading", "broker", "secondary market", "dividend", "sebon", "share market", "शेयर", "सेयर", "नेप्से", "आइपिओ", "लाभांश", "धितोपत्र"]),
        ("Sports News", ["/sports/", "/khelkud/", "/khel/", "sport", "sports", "cricket", "football", "soccer", "match", "cup", "league", "goal", "wicket", "stadium", "tournament", "messi", "ronaldo", "fifa", "icc", "ipl", "olympic", "athletics", "champion", "trophy", "can", "khel", "khelkud", "खेल", "क्रिकेट", "फुटबल", "गोल", "म्याच", "कप", "लिग", "विकेट", "रंगशाला", "रङ्गशाला", "च्याम्पियन", "क्यान", "ओलिम्पिक", "खेलाडी", "प्रतियोगिता", "टूर्नामेंट", "खेलकुद"]),
        ("Entertainment News", ["/entertainment/", "/manoranjan/", "/cinema/", "entertainment", "manoranjan", "movie", "film", "actor", "actress", "cinema", "music", "song", "award", "bollywood", "hollywood", "celebrity", "theater", "show", "artist", "album", "kala", "मनोरञ्जन", "चलचित्र", "फिल्म", "नायक", "नायिका", "सिनेमा", "संगीत", "सङ्गीत", "गीत", "अवार्ड", "कलाकार", "गायक", "गायिका", "शो", "थिएटर", "कला"]),
        ("Health News", ["/health/", "/swasthya/", "health", "swasthya", "hospital", "doctor", "disease", "virus", "vaccine", "lifestyle", "food", "fitness", "medicine", "patient", "epidemic", "wellness", "diet", "स्वास्थ्य", "हेल्थ", "अस्पताल", "डाक्टर", "रोग", "भाइरस", "खोप", "जीवनशैली", "औषधि", "बिरामी", "उपचार", "महामारी", "खाद्यान्न", "स्वास्थ्यकर्मी"]),
        ("Technology News", ["/technology/", "/tech/", "/prabidhi/", "technology", "tech", "prabidhi", "ai", "app", "digital", "software", "mobile", "internet", "cyber", "google", "apple", "meta", "starlink", "computer", "gadget", "smartphone", "data", "robot", "प्रविधि", "ग्याजेट", "एप्लिकेसन", "एप", "डिजिटल", "सफ्टवेयर", "मोबाइल", "इन्टरनेट", "साइबर", "डेटा", "कम्प्युटर", "एआई", "स्मार्टफोन", "आर्टिफिसियल", "ग्याजेट्स"]),
        ("Political News", ["/politics/", "/rajniti/", "/rajneeti/", "politics", "political", "politic", "rajniti", "rajneeti", "minister", "prime minister", "government", "parliament", "election", "party", "policy", "supreme court", "president", "congress", "uml", "maoist", "politician", "cabinet", "constitution", "mp", "sarkar", "pradhanmantri", "राजनीति", "मन्त्री", "प्रधानमन्त्री", "सरकार", "संसद", "संसद्", "निर्वाचन", "चुनाव", "दल", "पार्टी", "अदालत", "सर्वोच्च", "राष्ट्रपति", "कांग्रेस", "एमाले", "माओवादी", "सांसद", "संविधान", "मन्त्रिपरिषद्", "नेता"]),
        ("Economic News", ["/economy/", "/economic/", "/arthik/", "economy", "economic", "arthik", "inflation", "revenue", "bhansa", "kinmel", "budget", "gdp", "growth", "remittance", "debt", "nrb", "central bank", "fiscal", "अर्थतन्त्र", "बजेट", "राजस्व", "विप्रेषण", "रेमिट्यान्स", "राष्ट्र बैंक", "जिडिपी", "मौद्रिक"]),
        ("Business News", ["/business/", "/wyapar/", "/bazar/", "business", "wyapar", "bazar", "market", "bank", "banking", "corporate", "tax", "investment", "trade", "dollar", "finance", "export", "import", "profit", "company", "उद्योग", "व्यापार", "बैंक", "लगानी", "नाफा", "घाटा", "बजार", "वित्त", "कारोबार", "बिजनेस"]),
        ("International News", ["/world/", "/international/", "/bidesh/", "/videsh/", "world", "international", "bidesh", "videsh", "global", "foreign", "us", "china", "india", "uk", "russia", "america", "विश्व", "अन्तर्राष्ट्रिय", "विदेश समाचार", "विदेश", "भारत", "चीन", "अमेरिका", "रसिया", "अन्तरराष्ट्रिय"]),
        ("National News", ["/national/", "/pradesh/", "/desh/", "national", "pradesh", "desh", "nepal", "kathmandu", "pokhara", "district", "province", "local", "palika", "राष्ट्रिय", "प्रदेश", "राष्ट्रिय समाचार", "प्रदेश समाचार", "नेपाल", "काठमाडौँ", "काठमाडौं", "पोखरा", "जिल्ला", "स्थानीय", "पालिका"])
    ]

    for cat_name, patterns in mappings:
        for pat in patterns:
            if pat.startswith("/") and pat.endswith("/"):
                if pat in url_path:
                    return cat_name

    for cat_name, patterns in mappings:
        for tag in feed_tags:
            clean_tag = tag.strip().lower()
            for pat in patterns:
                if not pat.startswith("/") and clean_tag == pat:
                    return cat_name

    return None

def determine_categories(entry, title, link, clean_desc, source_name, pub_date=None):
    categories = set()
    explicit_cat = get_explicit_category(entry, link, source_name)

    if explicit_cat:
        categories.add(explicit_cat)

    is_recent = False
    if pub_date:
        parsed_dt = safe_parse_dt(pub_date)
        now_dt = datetime.now(NEPAL_TZ)
        if parsed_dt != datetime.min.replace(tzinfo=NEPAL_TZ):
            diff = now_dt - parsed_dt
            if timedelta(hours=-1) <= diff <= timedelta(hours=4):
                is_recent = True

    breaking_kw = [
        "breaking", "urgent", "update", "live", "alert", "flash", "latest", "special", "main",
        "ब्रेकिङ", "अपडेट", "लाइभ", "अध्यावधिक", "तत्काल", "प्रमुख समाचार", "विशेष", "मुख्य", "प्रमुख",
        "मुख्य समाचार", "ताजा खबर", "ताजा न्युज", "भर्खरै", "flash news", "ताजा समाचार"
    ]

    link_lower = link.lower()
    title_lower = title.lower()
    desc_lower = clean_desc.lower()
    feed_cat_str = " ".join([t.term.lower() for t in getattr(entry, 'tags', []) if hasattr(t, 'term')])

    has_breaking_kw = check_keywords(title_lower, breaking_kw) or \
                      check_keywords(desc_lower, breaking_kw) or \
                      check_keywords(feed_cat_str, breaking_kw) or \
                      any(slug in link_lower for slug in ['/breaking/', '/breaking-news/'])

    if is_recent and has_breaking_kw:
        categories.add("Breaking News")

    return sorted(list(categories))

def detect_multi_source_breaking_news(items):
    now_dt = datetime.now(NEPAL_TZ)
    recent_items = []

    for item in items:
        pdate = item.get("pub_date")
        if pdate:
            dt = safe_parse_dt(pdate)
            if dt != datetime.min.replace(tzinfo=NEPAL_TZ):
                diff = now_dt - dt
                if timedelta(hours=-1) <= diff <= timedelta(hours=6):
                    recent_items.append(item)

    def get_tokens(text):
        tokens = set(re.findall(r'[\u0900-\u097F\w]{3,}', text.lower()))
        return tokens - NEP_STOP_WORDS

    source_matches = {id(item): {item.get("source_name")} for item in recent_items}

    for i in range(len(recent_items)):
        item_a = recent_items[i]
        tokens_a = get_tokens(item_a.get("title", ""))
        if not tokens_a:
            continue

        for j in range(i + 1, len(recent_items)):
            item_b = recent_items[j]
            if item_a.get("source_name") == item_b.get("source_name"):
                continue

            tokens_b = get_tokens(item_b.get("title", ""))
            if not tokens_b:
                continue

            intersection = tokens_a.intersection(tokens_b)
            union = tokens_a.union(tokens_b)

            if union:
                jaccard_score = len(intersection) / len(union)
                if jaccard_score >= 0.30 or len(intersection) >= 3:
                    source_matches[id(item_a)].add(item_b.get("source_name"))
                    source_matches[id(item_b)].add(item_a.get("source_name"))

    for item in recent_items:
        distinct_sources = source_matches[id(item)]
        if len(distinct_sources) >= 2:
            if "Breaking News" not in item["categories"]:
                item["categories"].append("Breaking News")
                item["categories"].sort()

def titles_are_duplicate(title1, title2):
    if not title1 or not title2:
        return False

    norm1 = re.sub(r'[^\u0900-\u097F\w]', '', title1.lower())
    norm2 = re.sub(r'[^\u0900-\u097F\w]', '', title2.lower())
    if norm1 == norm2:
        return True

    tokens1 = set(re.findall(r'[\u0900-\u097F\w]{2,}', title1.lower())) - NEP_STOP_WORDS
    tokens2 = set(re.findall(r'[\u0900-\u097F\w]{2,}', title2.lower())) - NEP_STOP_WORDS

    if not tokens1 or not tokens2:
        return False

    intersection = tokens1.intersection(tokens2)
    union = tokens1.union(tokens2)

    jaccard = len(intersection) / len(union)
    return jaccard >= 0.70

def deduplicate_cross_source(items):
    unique_items = []

    for item in items:
        is_dup = False
        dt_item = safe_parse_dt(item.get("pub_date"))

        for u_item in unique_items:
            dt_u = safe_parse_dt(u_item.get("pub_date"))

            if dt_item != datetime.min.replace(tzinfo=NEPAL_TZ) and dt_u != datetime.min.replace(tzinfo=NEPAL_TZ):
                if abs((dt_item - dt_u).total_seconds()) > 172800:
                    continue

            if titles_are_duplicate(item.get("title", ""), u_item.get("title", "")):
                is_dup = True

                if "Breaking News" in item.get("categories", []) and "Breaking News" not in u_item.get("categories", []):
                    u_item["categories"].append("Breaking News")
                    u_item["categories"].sort()

                if not u_item.get("image_url") and item.get("image_url"):
                    u_item["image_url"] = item["image_url"]

                break

        if not is_dup:
            unique_items.append(item)

    return unique_items

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
            item['source_name'],
            item['pub_date']
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

            combined_items.append(ex)

    detect_multi_source_breaking_news(combined_items)

    combined_items.sort(key=lambda x: safe_parse_dt(x.get("pub_date")), reverse=True)

    deduplicated_items = deduplicate_cross_source(combined_items)

    final_news = deduplicated_items[:MAX_NEWS_ITEMS]

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    temp_file = OUTPUT_FILE + ".tmp"
    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(final_news, f, ensure_ascii=False, indent=2)

    os.replace(temp_file, OUTPUT_FILE)

    logging.info(f"Successfully generated {OUTPUT_FILE} with {len(final_news)} news items.")

if __name__ == "__main__":
    fetch_and_store_news()
