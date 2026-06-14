"""
Multi‑source stock media aggregator – fetches images & videos from all available free sources.
Uses official APIs where possible, web scraping otherwise. Respects rate limits.
"""

import asyncio
import hashlib
import logging
import os
import re
import time
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from urllib.parse import quote_plus, urlparse

import aiohttp
from aiohttp import ClientSession, ClientTimeout, TCPConnector
from bs4 import BeautifulSoup

from utils import download_file, ensure_output_dir

logger = logging.getLogger(__name__)

# --------------------------- Configuration ---------------------------
MAX_MEDIA_PER_SOURCE = 5          # how many results to keep from each source per query
MAX_CONCURRENT_REQUESTS = 10
USER_AGENT = "YT-Auto-Video-Gen/1.0 (Educational Project; contact@example.com)"

# API keys from environment (set as GitHub Secrets)
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY", "")
PIXABAY_API_KEY = os.getenv("PIXABAY_API_KEY", "")
UNSPLASH_ACCESS_KEY = os.getenv("UNSPLASH_ACCESS_KEY", "")
BURST_API_KEY = os.getenv("BURST_API_KEY", "")

# --------------------------- Async HTTP helpers ---------------------------

async def _fetch_json(session: ClientSession, url: str, headers: dict = None, params: dict = None) -> dict:
    """Async GET returning JSON."""
    try:
        async with session.get(url, headers=headers, params=params, timeout=30) as resp:
            resp.raise_for_status()
            return await resp.json()
    except Exception as e:
        logger.debug(f"API call failed {url}: {e}")
        return {}

async def _fetch_html(session: ClientSession, url: str, headers: dict = None) -> Optional[str]:
    """Async GET returning HTML text."""
    try:
        async with session.get(url, headers=headers, timeout=30) as resp:
            resp.raise_for_status()
            return await resp.text()
    except Exception as e:
        logger.debug(f"HTML fetch failed {url}: {e}")
        return None

# --------------------------- Source: Pexels (Image + Video) ---------------------------

async def _search_pexels(session: ClientSession, query: str, media_type: str = "both") -> List[Dict]:
    """Search Pexels API for images and/or videos. Returns list of media items."""
    if not PEXELS_API_KEY:
        return []

    headers = {"Authorization": PEXELS_API_KEY}
    results = []

    # Images
    if media_type in ("both", "image"):
        url = "https://api.pexels.com/v1/search"
        params = {"query": query, "per_page": MAX_MEDIA_PER_SOURCE}
        data = await _fetch_json(session, url, headers=headers, params=params)
        for photo in data.get("photos", []):
            results.append({
                "url": photo["src"]["original"],
                "title": photo.get("alt", ""),
                "source": "Pexels (Image)",
                "type": "image",
                "thumbnail_url": photo["src"]["tiny"]
            })

    # Videos
    if media_type in ("both", "video"):
        url_vid = "https://api.pexels.com/videos/search"
        params_vid = {"query": query, "per_page": MAX_MEDIA_PER_SOURCE}
        data_vid = await _fetch_json(session, url_vid, headers=headers, params=params_vid)
        for video in data_vid.get("videos", []):
            video_files = video.get("video_files", [])
            if video_files:
                best = max(video_files, key=lambda x: x.get("width", 0) * x.get("height", 0))
                results.append({
                    "url": best["link"],
                    "title": video.get("url", "").split("/")[-1],
                    "source": "Pexels (Video)",
                    "type": "video",
                    "thumbnail_url": video.get("image", "")
                })

    return results[:MAX_MEDIA_PER_SOURCE]

# --------------------------- Source: Pixabay (Image + Video) ---------------------------

async def _search_pixabay(session: ClientSession, query: str, media_type: str = "both") -> List[Dict]:
    """Search Pixabay API for images and videos."""
    if not PIXABAY_API_KEY:
        return []

    results = []
    base_url = "https://pixabay.com/api/"
    if media_type in ("both", "image"):
        params = {"key": PIXABAY_API_KEY, "q": query, "image_type": "photo", "per_page": MAX_MEDIA_PER_SOURCE}
        data = await _fetch_json(session, base_url, params=params)
        for hit in data.get("hits", []):
            results.append({
                "url": hit["largeImageURL"],
                "title": hit.get("tags", ""),
                "source": "Pixabay (Image)",
                "type": "image",
                "thumbnail_url": hit["previewURL"]
            })

    if media_type in ("both", "video"):
        params_vid = {"key": PIXABAY_API_KEY, "q": query, "video_type": "film", "per_page": MAX_MEDIA_PER_SOURCE}
        data_vid = await _fetch_json(session, base_url + "videos/", params=params_vid)
        for hit in data_vid.get("hits", []):
            videos = hit.get("videos", {})
            for size in ["large", "medium", "small"]:
                if size in videos:
                    results.append({
                        "url": videos[size]["url"],
                        "title": hit.get("tags", ""),
                        "source": "Pixabay (Video)",
                        "type": "video",
                        "thumbnail_url": hit.get("picture_id", "")
                    })
                    break

    return results[:MAX_MEDIA_PER_SOURCE]

# --------------------------- Source: Unsplash (Image only) ---------------------------

async def _search_unsplash(session: ClientSession, query: str) -> List[Dict]:
    """Search Unsplash API for photos."""
    if not UNSPLASH_ACCESS_KEY:
        return []

    url = "https://api.unsplash.com/search/photos"
    headers = {"Authorization": f"Client-ID {UNSPLASH_ACCESS_KEY}"}
    params = {"query": query, "per_page": MAX_MEDIA_PER_SOURCE}
    data = await _fetch_json(session, url, headers=headers, params=params)
    results = []
    for photo in data.get("results", []):
        results.append({
            "url": photo["urls"]["regular"],
            "title": photo.get("description") or photo.get("alt_description", ""),
            "source": "Unsplash",
            "type": "image",
            "thumbnail_url": photo["urls"]["thumb"]
        })
    return results

# --------------------------- Source: Burst (Shopify) ---------------------------

async def _search_burst(session: ClientSession, query: str) -> List[Dict]:
    """Search Burst free stock photos (Shopify)."""
    if not BURST_API_KEY:
        return []

    url = f"https://burst.shopify.com/api/v1/search?q={quote_plus(query)}&per_page={MAX_MEDIA_PER_SOURCE}"
    headers = {"Authorization": f"Bearer {BURST_API_KEY}"}
    data = await _fetch_json(session, url, headers=headers)
    results = []
    for item in data.get("results", []):
        results.append({
            "url": item["image"]["url"],
            "title": item.get("title", ""),
            "source": "Burst (Shopify)",
            "type": "image",
            "thumbnail_url": item["image"]["thumb"]["url"]
        })
    return results

# --------------------------- Source: Mixkit (Video) ---------------------------

async def _search_mixkit(session: ClientSession, query: str) -> List[Dict]:
    """Scrape Mixkit free stock videos."""
    url = f"https://mixkit.co/free-stock-video/?q={quote_plus(query)}"
    html = await _fetch_html(session, url, headers={"User-Agent": USER_AGENT})
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    results = []
    for card in soup.select(".item-grid__item, .video-card")[:MAX_MEDIA_PER_SOURCE]:
        a_tag = card.find("a", href=True)
        if not a_tag:
            continue
        video_page = "https://mixkit.co" + a_tag["href"] if a_tag["href"].startswith("/") else a_tag["href"]
        img = card.find("img")
        thumb = img["src"] if img else ""
        title_tag = card.find("h3") or card.find("p")
        title = title_tag.get_text(strip=True) if title_tag else ""
        results.append({
            "url": video_page,
            "title": title,
            "source": "Mixkit",
            "type": "video",
            "thumbnail_url": thumb,
            "_needs_page": True
        })
    return results

# --------------------------- Source: Coverr (Video) ---------------------------

async def _search_coverr(session: ClientSession, query: str) -> List[Dict]:
    """Scrape Coverr free stock videos."""
    url = f"https://coverr.co/s?q={quote_plus(query)}"
    html = await _fetch_html(session, url, headers={"User-Agent": USER_AGENT})
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    results = []
    for item in soup.select(".video-item, .video-entry")[:MAX_MEDIA_PER_SOURCE]:
        a_tag = item.find("a", href=re.compile(r"/video/|/download/"))
        if not a_tag:
            continue
        video_page = "https://coverr.co" + a_tag["href"]
        thumb = ""
        img = item.find("img")
        if img:
            thumb = img.get("src") or img.get("data-src", "")
        title = item.get("data-title", "") or (img["alt"] if img else "")
        results.append({
            "url": video_page,
            "title": title,
            "source": "Coverr",
            "type": "video",
            "thumbnail_url": thumb,
            "_needs_page": True
        })
    return results

# --------------------------- Source: Videvo (Video) ---------------------------

async def _search_videvo(session: ClientSession, query: str) -> List[Dict]:
    """Scrape Videvo free stock videos (requires attribution)."""
    url = f"https://www.videvo.net/search/?q={quote_plus(query)}"
    html = await _fetch_html(session, url, headers={"User-Agent": USER_AGENT})
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    results = []
    for item in soup.select(".video-thumb, .video-item")[:MAX_MEDIA_PER_SOURCE]:
        a_tag = item.find("a", href=re.compile(r"/stock-video/"))
        if not a_tag:
            continue
        video_page = a_tag["href"]
        if not video_page.startswith("http"):
            video_page = "https://www.videvo.net" + video_page
        img = item.find("img")
        thumb = img["src"] if img else ""
        title = img["alt"] if img else ""
        results.append({
            "url": video_page,
            "title": title,
            "source": "Videvo",
            "type": "video",
            "thumbnail_url": thumb,
            "_needs_page": True
        })
    return results

# --------------------------- Download helpers for video scraped pages ---------------------------

async def _resolve_mixkit_video_url(session: ClientSession, video_page_url: str) -> Tuple[Optional[str], str]:
    """Given a Mixkit video page, extract the direct MP4 download link and a better title."""
    html = await _fetch_html(session, video_page_url)
    if not html:
        return None, ""
    soup = BeautifulSoup(html, "html.parser")
    # Title from page heading
    title = ""
    title_tag = soup.find("h1") or soup.find("title")
    if title_tag:
        title = title_tag.get_text(strip=True)
    # Download link
    download_btn = soup.find("a", {"class": "download-btn"}) or soup.find("a", text=re.compile(r"Download", re.I))
    if download_btn and download_btn.get("href"):
        return download_btn["href"], title
    video_tag = soup.find("video")
    if video_tag and video_tag.get("src"):
        return video_tag["src"], title
    for link in soup.find_all("a", href=True):
        if link["href"].endswith(".mp4"):
            return link["href"], title
    return None, title

async def _resolve_coverr_video_url(session: ClientSession, video_page_url: str) -> Tuple[Optional[str], str]:
    """Given a Coverr video page, extract direct MP4 and a better title."""
    html = await _fetch_html(session, video_page_url)
    if not html:
        return None, ""
    soup = BeautifulSoup(html, "html.parser")
    title = ""
    title_tag = soup.find("h1") or soup.find("title")
    if title_tag:
        title = title_tag.get_text(strip=True)
    source = soup.find("source")
    if source and source.get("src"):
        return source["src"], title
    for a in soup.find_all("a", href=re.compile(r'\.mp4')):
        return a["href"], title
    return None, title

async def _resolve_videvo_video_url(session: ClientSession, video_page_url: str) -> Tuple[Optional[str], str]:
    """Given a Videvo page, extract free clip download URL and a better title."""
    html = await _fetch_html(session, video_page_url)
    if not html:
        return None, ""
    soup = BeautifulSoup(html, "html.parser")
    title = ""
    title_tag = soup.find("h1") or soup.find("title")
    if title_tag:
        title = title_tag.get_text(strip=True)
    scripts = soup.find_all("script")
    for script in scripts:
        if script.string and "clipData" in script.string:
            import json
            try:
                match = re.search(r'clipData\s*=\s*(\{.*?\});', script.string, re.DOTALL)
                if match:
                    data = json.loads(match.group(1))
                    if "url" in data:
                        return data["url"], title
            except:
                pass
    for a in soup.find_all("a", href=re.compile(r'\.mp4')):
        return a["href"], title
    return None, title

# --------------------------- Aggregator ---------------------------

async def _fetch_media_for_one_scene(session: ClientSession, keywords: List[str], temp_dir: Path,
                                     scene_idx: int, max_total: int = 5) -> List[Dict]:
    """
    For one scene, search all sources and return list of media dicts.
    Downloads each media file to temp_dir/scene_{scene_idx}/.
    """
    query = " ".join(keywords[:5])
    if not query.strip():
        query = "abstract background"

    scene_folder = temp_dir / f"scene_{scene_idx}"
    ensure_output_dir(scene_folder)

    tasks = [
        _search_pexels(session, query),
        _search_pixabay(session, query),
        _search_unsplash(session, query),
        _search_burst(session, query),
        _search_mixkit(session, query),
        _search_coverr(session, query),
        _search_videvo(session, query),
    ]
    results_nested = await asyncio.gather(*tasks)
    all_items = []
    for res in results_nested:
        all_items.extend(res)

    # Resolve video URLs that need page scraping, and update title if possible
    for item in all_items:
        if item.get("_needs_page"):
            item.pop("_needs_page", None)
            real_url = None
            page_title = ""
            if "mixkit" in item["url"]:
                real_url, page_title = await _resolve_mixkit_video_url(session, item["url"])
            elif "coverr" in item["url"]:
                real_url, page_title = await _resolve_coverr_video_url(session, item["url"])
            elif "videvo" in item["url"]:
                real_url, page_title = await _resolve_videvo_video_url(session, item["url"])
            if real_url:
                item["url"] = real_url
                if page_title:
                    item["title"] = page_title   # <-- better title from video page
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

    final_items = all_items[:max_total]
    final_items = [i for i in final_items if i.get("downloaded")]
    return final_items

# --------------------------- Public API ---------------------------

async def fetch_media_for_scenes(scene_keywords_list: List[List[str]], temp_dir: Path,
                                 max_per_scene: int = 5) -> List[List[Dict]]:
    """
    Given a list of keyword lists (one per scene), download up to max_per_scene media per scene.
    Returns list of media lists, each media dict has keys:
      file_path, title, source, type, url, thumbnail_url
    """
    connector = TCPConnector(limit=MAX_CONCURRENT_REQUESTS)
    timeout = ClientTimeout(total=300)
    async with aiohttp.ClientSession(connector=connector, timeout=timeout,
                                     headers={"User-Agent": USER_AGENT}) as session:
        tasks = []
        for idx, keywords in enumerate(scene_keywords_list):
            tasks.append(_fetch_media_for_one_scene(session, keywords, temp_dir, idx, max_per_scene))
        all_results = await asyncio.gather(*tasks)
    return all_results
