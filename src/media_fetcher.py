"""
Multi‑source stock media aggregator – fetches images & videos from all available free sources.
Includes free image sources that need NO API key.
Supports visual_queries_list for thematic search, and image search via
Bing Image Search scraping (replaces the broken Google Images scraper).

For each scene we fetch BOTH images and videos from every available source
(API-key sources + key-free scraped/API sources), then return a mixed pool.
media_selector.py picks the best item from that pool regardless of type, so
a scene can end up with either an image or a video — whichever is most
relevant — exactly as requested.
"""

import asyncio, hashlib, logging, os, re, json
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
USER_AGENT = "YT-Auto-Video-Gen/1.0 (Educational Project)"

# API keys from environment (set as GitHub Secrets)
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY", "")
PIXABAY_API_KEY = os.getenv("PIXABAY_API_KEY", "")
UNSPLASH_ACCESS_KEY = os.getenv("UNSPLASH_ACCESS_KEY", "")
BURST_API_KEY = os.getenv("BURST_API_KEY", "")

# Warn (once, at import time) about missing API keys so it's obvious in the
# GitHub Actions logs why certain sources return zero results. These sources
# generally have the BEST topical relevance (real titles/tags), so missing
# keys directly hurts match quality.
if not PEXELS_API_KEY:
    logger.warning("PEXELS_API_KEY not set — Pexels image/video search disabled. "
                    "Get a free key at https://www.pexels.com/api/ and add it as a GitHub Secret.")
if not PIXABAY_API_KEY:
    logger.warning("PIXABAY_API_KEY not set — Pixabay image/video search disabled. "
                    "Get a free key at https://pixabay.com/api/docs/ and add it as a GitHub Secret.")
if not UNSPLASH_ACCESS_KEY:
    logger.warning("UNSPLASH_ACCESS_KEY not set — Unsplash image search disabled. "
                    "Get a free key at https://unsplash.com/developers and add it as a GitHub Secret.")

# ---------- Async HTTP helpers ----------
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

# ---------- Original API Sources ----------

async def _search_pexels(session, query, media_type="both"):
    if not PEXELS_API_KEY:
        return []
    headers = {"Authorization": PEXELS_API_KEY}
    results = []
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

async def _search_pixabay(session, query, media_type="both"):
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

async def _search_unsplash(session, query):
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

async def _search_burst(session, query):
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

# ---------- Scraped Video Sources ----------

async def _search_mixkit(session, query):
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

async def _search_coverr(session, query):
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

async def _search_videvo(session, query):
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

# ---------- Resolvers (return Tuple[url, title]) ----------

async def _resolve_mixkit_video_url(session, video_page_url):
    html = await _fetch_html(session, video_page_url)
    if not html:
        return None, ""
    soup = BeautifulSoup(html, "html.parser")
    title = ""
    title_tag = soup.find("h1") or soup.find("title")
    if title_tag:
        title = title_tag.get_text(strip=True)
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

async def _resolve_coverr_video_url(session, video_page_url):
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

async def _resolve_videvo_video_url(session, video_page_url):
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

# ---------- NEW FREE IMAGE SOURCES (no API key needed) ----------

async def _search_wikimedia(session, query):
    url = "https://commons.wikimedia.org/w/api.php"
    params = {
        "action": "query",
        "list": "search",
        "srsearch": query,
        "srnamespace": "6",
        "format": "json",
        "srlimit": MAX_MEDIA_PER_SOURCE,
    }
    data = await _fetch_json(session, url, params=params)
    if not data or "query" not in data:
        return []
    results = []
    for item in data["query"]["search"]:
        title = item["title"]
        img_url = await _get_wikimedia_image_url(session, title)
        if img_url:
            results.append({
                "url": img_url,
                "title": title.replace("File:", "").replace("_", " "),
                "source": "Wikimedia Commons",
                "type": "image",
                "thumbnail_url": img_url,
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

async def _search_stocksnap(session, query):
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
        if src.startswith("/"):
            src = "https://stocksnap.io" + src
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

# ---------- Bing Image Search Scraping (replaces broken Google Images scraper) ----------
async def _search_bing_images(session, query):
    """
    Scrape Bing Image Search results for direct, high-resolution image URLs.

    Bing's image search results page embeds each result's metadata as a JSON
    blob inside the `m` attribute of `<a class="iusc">` tags, e.g.:
        <a class="iusc" m='{"murl":"https://...","t":"Some Title", ...}'>
    `murl` is the original (often high-res) image URL. This is far more
    stable than Google's heavily obfuscated/JS-rendered results and needs
    no API key.
    """
    url = f"https://www.bing.com/images/search?q={quote_plus(query)}&form=HDRSC2&first=1"
    html = await _fetch_html(session, url, headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"})
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    results = []
    for a_tag in soup.select("a.iusc")[: MAX_MEDIA_PER_SOURCE * 2]:
        meta_raw = a_tag.get("m")
        if not meta_raw:
            continue
        try:
            meta = json.loads(meta_raw)
        except (json.JSONDecodeError, TypeError):
            continue

        img_url = meta.get("murl")
        if not img_url or not img_url.startswith("http"):
            continue

        title = meta.get("t", "") or query
        thumb = meta.get("turl", img_url)

        results.append({
            "url": img_url,
            "title": title,
            "source": "Bing Images",
            "type": "image",
            "thumbnail_url": thumb,
        })
        if len(results) >= MAX_MEDIA_PER_SOURCE:
            break

    return results

# ---------- Aggregator ----------

async def _run_all_sources(session, query):
    """Run every media source concurrently for a single query string."""
    tasks = [
        _search_pexels(session, query),
        _search_pixabay(session, query),
        _search_unsplash(session, query),
        _search_burst(session, query),
        _search_mixkit(session, query),
        _search_coverr(session, query),
        _search_videvo(session, query),
        _search_wikimedia(session, query),
        _search_stocksnap(session, query),
        _search_bing_images(session, query),
    ]
    results_nested = await asyncio.gather(*tasks, return_exceptions=True)
    all_items = []
    for res in results_nested:
        if isinstance(res, Exception):
            logger.debug(f"Source raised exception for query '{query}': {res}")
            continue
        all_items.extend(res)
    return all_items


async def _fetch_media_for_one_scene(session, keywords, temp_dir, scene_idx,
                                     visual_queries=None, max_total=5):
    primary_query = " ".join(keywords[:5])
    if not primary_query.strip():
        primary_query = "abstract background"

    scene_folder = temp_dir / f"scene_{scene_idx}"
    ensure_output_dir(scene_folder)

    # Run searches for:
    #  1. The primary keyword query (literal topic terms from the script)
    #  2. Each thematic visual query individually (e.g. "casino chips",
    #     "white house speech") — kept separate so each thematic phrase is
    #     a clean, focused search term rather than being mashed together
    #     with the primary keywords into one long noisy query.
    queries_to_run = [primary_query]
    if visual_queries:
        queries_to_run.extend(visual_queries[:3])

    results_nested = await asyncio.gather(*[_run_all_sources(session, q) for q in queries_to_run])
    all_items = []
    for res in results_nested:
        all_items.extend(res)

    # Resolve video URLs that need page scraping
    resolved_items = []
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
        resolved_items.append(item)

    # Deduplicate by URL before downloading (avoid downloading the same
    # asset multiple times if it shows up across queries/sources).
    deduped = []
    seen_urls = set()
    for item in resolved_items:
        if item["url"] not in seen_urls:
            deduped.append(item)
            seen_urls.add(item["url"])

    # Download everything (bounded by overall MAX_CONCURRENT_REQUESTS via
    # the session connector). We download more than max_total so that
    # downstream selection has a real pool of both images AND videos to
    # choose from, then trim afterwards.
    download_limit = max(max_total * 4, 20)
    candidates = deduped[:download_limit]

    for item in candidates:
        ext = ".mp4" if item["type"] == "video" else ".jpg"
        fname = hashlib.md5(item["url"].encode()).hexdigest() + ext
        file_path = scene_folder / fname
        if await asyncio.to_thread(download_file, item["url"], file_path):
            item["file_path"] = str(file_path)
            item["downloaded"] = True
        else:
            item["downloaded"] = False
        if not item.get("title"):
            item["title"] = ""

    downloaded = [i for i in candidates if i.get("downloaded")]

    # Interleave images and videos so neither type is starved when trimming
    # to max_total (e.g. if videos happen to come back first across all
    # sources, images wouldn't otherwise get a chance).
    images = [i for i in downloaded if i.get("type") == "image"]
    videos = [i for i in downloaded if i.get("type") != "image"]

    final_items = []
    i_idx, v_idx = 0, 0
    while len(final_items) < max_total and (i_idx < len(images) or v_idx < len(videos)):
        if i_idx < len(images):
            final_items.append(images[i_idx])
            i_idx += 1
        if len(final_items) >= max_total:
            break
        if v_idx < len(videos):
            final_items.append(videos[v_idx])
            v_idx += 1

    if not final_items:
        logger.warning(f"Scene {scene_idx}: no media could be downloaded for query '{primary_query}' "
                        f"(thematic queries: {visual_queries}).")
    else:
        logger.info(f"Scene {scene_idx}: gathered {len(final_items)} media items "
                     f"({sum(1 for i in final_items if i['type']=='image')} images, "
                     f"{sum(1 for i in final_items if i['type']=='video')} videos) "
                     f"from sources: {sorted(set(i['source'] for i in final_items))}")

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
