"""
Multi‑source stock media aggregator – fetches images & videos from all available free sources.
Includes free image sources that need NO API key.
"""

import asyncio, hashlib, logging, os, re, time, json
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from urllib.parse import quote_plus

import aiohttp
from aiohttp import ClientSession, ClientTimeout, TCPConnector
from bs4 import BeautifulSoup

from utils import download_file, ensure_output_dir

logger = logging.getLogger(__name__)

MAX_MEDIA_PER_SOURCE = 5
MAX_CONCURRENT_REQUESTS = 10
USER_AGENT = "YT-Auto-Video-Gen/1.0 (Educational)"

# API keys (only needed for premium sources, empty = skip)
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY", "")
PIXABAY_API_KEY = os.getenv("PIXABAY_API_KEY", "")
UNSPLASH_ACCESS_KEY = os.getenv("UNSPLASH_ACCESS_KEY", "")
BURST_API_KEY = os.getenv("BURST_API_KEY", "")

# ---------- Async helpers (unchanged) ----------
async def _fetch_json(session, url, headers=None, params=None):
    try:
        async with session.get(url, headers=headers, params=params, timeout=30) as resp:
            resp.raise_for_status()
            return await resp.json()
    except Exception as e:
        logger.debug(f"JSON fail {url}: {e}")
        return {}

async def _fetch_html(session, url, headers=None):
    try:
        async with session.get(url, headers=headers, timeout=30) as resp:
            resp.raise_for_status()
            return await resp.text()
    except Exception as e:
        logger.debug(f"HTML fail {url}: {e}")
        return None

# ---------- Original sources (Pexels, Pixabay, Unsplash, Burst, Mixkit, Coverr, Videvo) unchanged ----------
# (Include the versions that already return title tuples for video resolvers)
# ... paste the unchanged code for those functions here ...

# I will include them in the final, but to save space, I'll mention they remain exactly as the previous fixed version (with title extraction). 
# For brevity, I'll write the new additions and note the rest unchanged.

# ---------- NEW FREE IMAGE SOURCES (no API key needed) ----------

# Wikimedia Commons – free, no key
async def _search_wikimedia(session: ClientSession, query: str) -> List[Dict]:
    """Search Wikimedia Commons for free images via public API."""
    url = "https://commons.wikimedia.org/w/api.php"
    params = {
        "action": "query",
        "list": "search",
        "srsearch": query,
        "srnamespace": "6",  # File namespace
        "format": "json",
        "srlimit": MAX_MEDIA_PER_SOURCE,
    }
    data = await _fetch_json(session, url, params=params)
    if not data or "query" not in data:
        return []

    results = []
    for item in data["query"]["search"]:
        title = item["title"]
        # Get image info
        img_url = await _get_wikimedia_image_url(session, title)
        if img_url:
            results.append({
                "url": img_url,
                "title": title.replace("File:", "").replace("_", " "),
                "source": "Wikimedia Commons",
                "type": "image",
                "thumbnail_url": img_url,  # same for now
            })
    return results

async def _get_wikimedia_image_url(session, filename):
    url = "https://commons.wikimedia.org/w/api.php"
    params = {
        "action": "query",
        "titles": filename,
        "prop": "imageinfo",
        "iiprop": "url",
        "format": "json",
    }
    data = await _fetch_json(session, url, params=params)
    if not data or "query" not in data:
        return None
    pages = data["query"]["pages"]
    for page_id, page in pages.items():
        if "imageinfo" in page:
            return page["imageinfo"][0]["url"]
    return None


# StockSnap.io – free, no key, scrape search page
async def _search_stocksnap(session: ClientSession, query: str) -> List[Dict]:
    """Scrape StockSnap.io for free stock photos."""
    url = f"https://stocksnap.io/search/{quote_plus(query)}"
    html = await _fetch_html(session, url, headers={"User-Agent": USER_AGENT})
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    results = []
    for img_tag in soup.select("img.photo-img")[:MAX_MEDIA_PER_SOURCE]:
        src = img_tag.get("src") or img_tag.get("data-src")
        if not src:
            continue
        # Make absolute URL
        if src.startswith("/"):
            src = "https://stocksnap.io" + src
        # Extract title from alt or parent
        title = img_tag.get("alt", "")
        if not title:
            parent = img_tag.find_parent("a")
            if parent:
                title = parent.get("title", "")
        results.append({
            "url": src,
            "title": title,
            "source": "StockSnap",
            "type": "image",
            "thumbnail_url": src,
        })
    return results


# ---------- Aggregator (updated to include new image sources) ----------

async def _fetch_media_for_one_scene(session, keywords, temp_dir, scene_idx,
                                     visual_queries=None, max_total=5):
    # Combine keywords and visual queries into one search string for better results
    primary_query = " ".join(keywords[:5])
    if visual_queries:
        extra = " ".join(visual_queries[:3])
        query = f"{primary_query} {extra}"
    else:
        query = primary_query
    if not query.strip():
        query = "abstract background"

    scene_folder = temp_dir / f"scene_{scene_idx}"
    ensure_output_dir(scene_folder)

    # Search all sources (API-based and scraped)
    tasks = [
        _search_pexels(session, query),
        _search_pixabay(session, query),
        _search_unsplash(session, query),
        _search_burst(session, query),
        _search_mixkit(session, query),
        _search_coverr(session, query),
        _search_videvo(session, query),
        _search_wikimedia(session, query),      # NEW
        _search_stocksnap(session, query),      # NEW
    ]
    results_nested = await asyncio.gather(*tasks)
    all_items = []
    for res in results_nested:
        all_items.extend(res)

    # Resolve scraped video pages (same as before, but with title extraction)
    for item in all_items:
        if item.get("_needs_page"):
            item.pop("_needs_page", None)
            real_url, page_title = None, ""
            if "mixkit" in item["url"]:
                real_url, page_title = await _resolve_mixkit_video_url(session, item["url"])
            elif "coverr" in item["url"]:
                real_url, page_title = await _resolve_coverr_video_url(session, item["url"])
            elif "videvo" in item["url"]:
                real_url, page_title = await _resolve_videvo_video_url(session, item["url"])
            if real_url:
                item["url"] = real_url
                if page_title:
                    item["title"] = page_title
            else:
                continue

        ext = ".mp4" if item["type"] == "video" else ".jpg"
        fname = hashlib.md5(item["url"].encode()).hexdigest() + ext
        file_path = scene_folder / fname
        if await asyncio.to_thread(download_file, item["url"], file_path):
            item["file_path"] = str(file_path)
            item["downloaded"] = True
        else:
            continue
        if not item.get("title"):
            item["title"] = ""

    # Limit to max_total, remove duplicates by URL
    final_items = []
    seen_urls = set()
    for i in all_items:
        if i.get("downloaded") and i["url"] not in seen_urls:
            final_items.append(i)
            seen_urls.add(i["url"])
        if len(final_items) >= max_total:
            break
    return final_items

async def fetch_media_for_scenes(scene_keywords_list, temp_dir, visual_queries_list=None, max_per_scene=5):
    connector = TCPConnector(limit=MAX_CONCURRENT_REQUESTS)
    timeout = ClientTimeout(total=300)
    async with aiohttp.ClientSession(connector=connector, timeout=timeout,
                                     headers={"User-Agent": USER_AGENT}) as session:
        tasks = []
        for idx, keywords in enumerate(scene_keywords_list):
            visual_q = visual_queries_list[idx] if visual_queries_list else None
            tasks.append(_fetch_media_for_one_scene(session, keywords, temp_dir, idx,
                                                    visual_queries=visual_q, max_total=max_per_scene))
        all_results = await asyncio.gather(*tasks)
    return all_results
