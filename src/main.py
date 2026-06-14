#!/usr/bin/env python3
"""
YT Auto Video Generator - Main Orchestrator
Cloud-native, free, smart-matching video creation from script.
Now with visual theme injection and image Ken Burns effects.
"""

import os
import sys
import asyncio
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from scene_processor import split_script, extract_keywords_and_visual_queries
from media_fetcher import fetch_media_for_scenes
from media_selector import select_best_media
from voiceover_generator import generate_audio
from video_assembler import assemble_video
from utils import setup_logging, cleanup_temp_files, ensure_output_dir

SCRIPT = os.getenv("SCRIPT", "")
ASPECT = os.getenv("ASPECT", "16:9")
QUALITY = os.getenv("QUALITY", "1080p")
VOICEOVER_TYPE = os.getenv("VOICEOVER_TYPE", "tts")
AUDIO_URL = os.getenv("AUDIO_URL", "")

OUTPUT_DIR = Path("output")
OUTPUT_VIDEO = OUTPUT_DIR / "output.mp4"
TEMP_DIR = Path("temp_media")

RESOLUTIONS = {
    "1080p": (1920, 1080),
    "720p": (1280, 720),
}

def main():
    setup_logging()
    logger = logging.getLogger(__name__)

    logger.info("=" * 60)
    logger.info("YOUTUBE AUTO VIDEO GENERATOR - STARTED")
    logger.info(f"Aspect: {ASPECT} | Quality: {QUALITY} | Voiceover: {VOICEOVER_TYPE}")
    logger.info("=" * 60)

    if not SCRIPT.strip():
        logger.error("Script is empty. Exiting.")
        sys.exit(1)

    if VOICEOVER_TYPE == "custom_audio" and not AUDIO_URL:
        logger.error("Custom audio mode selected but no AUDIO_URL provided.")
        sys.exit(1)

    ensure_output_dir(OUTPUT_DIR)
    ensure_output_dir(TEMP_DIR)

    logger.info("📄 Processing script...")
    scenes_text = split_script(SCRIPT)
    logger.info(f"✓ Found {len(scenes_text)} scenes.")

    if not scenes_text:
        logger.error("No scenes could be extracted. Check script formatting.")
        sys.exit(1)

    # 4. Extract keywords AND visual search queries for each scene
    logger.info("🔍 Extracting keywords & visual queries...")
    scene_keywords = []
    scene_visual_queries = []
    for i, scene in enumerate(scenes_text):
        kw, visq = extract_keywords_and_visual_queries(scene)
        scene_keywords.append(kw)
        scene_visual_queries.append(visq)
        logger.info(f"  Scene {i+1}: {', '.join(kw[:5])} | Visuals: {', '.join(visq[:3])}")

    # 5. Fetch media with combined queries (original keywords + thematic visuals)
    logger.info("🌐 Fetching stock media with thematic queries...")
    try:
        media_results = asyncio.run(fetch_media_for_scenes(
            scene_keywords,
            temp_dir=TEMP_DIR,
            visual_queries_list=scene_visual_queries
        ))
    except Exception as e:
        logger.exception("Media fetching failed!")
        sys.exit(1)

    # 6. Smart selection with duplicate avoidance
    logger.info("🧠 Selecting best media for each scene...")
    selected_media = []
    used_urls = set()
    for i, (keywords, media_list) in enumerate(zip(scene_keywords, media_results)):
        if not media_list:
            logger.warning(f"⚠️  Scene {i+1}: No media found. Skipping scene.")
            selected_media.append(None)
            continue

        # Remove already used URLs to avoid repetition
        unused_media = [m for m in media_list if m.get("url") not in used_urls]
        if not unused_media:
            unused_media = media_list  # if all are used, allow reuse

        best = select_best_media(keywords, unused_media)
        if best:
            used_urls.add(best["url"])
        selected_media.append(best)
        logger.info(f"  Scene {i+1}: Selected → {best.get('source', 'unknown')} | {best.get('file_path', 'N/A')}")

    valid_scenes = []
    valid_media = []
    for scene_text, media in zip(scenes_text, selected_media):
        if media is not None:
            valid_scenes.append(scene_text)
            valid_media.append(media)
        else:
            logger.info(f"  Scene with text '{scene_text[:40]}...' skipped due to no media.")

    if not valid_scenes:
        logger.error("All scenes lacked media. Cannot generate video.")
        sys.exit(1)

    logger.info("🎙️  Preparing audio...")
    if VOICEOVER_TYPE == "no_audio":
        audio_path = None
        logger.info("🔇 No audio mode selected — video will be silent.")
    else:
        audio_path = TEMP_DIR / "voiceover.mp3"
        try:
            if VOICEOVER_TYPE == "tts":
                generate_audio("tts", text="\n".join(valid_scenes), output_path=audio_path)
            elif VOICEOVER_TYPE == "custom_audio":
                generate_audio("custom_audio", audio_url=AUDIO_URL, output_path=audio_path)
            else:
                logger.error(f"Unknown voiceover type: {VOICEOVER_TYPE}")
                sys.exit(1)
            logger.info(f"✓ Audio saved to {audio_path}")
        except Exception as e:
            logger.exception("Audio generation failed!")
            sys.exit(1)

    logger.info("🎬 Assembling video with Ken Burns effects and transitions...")
    width, height = RESOLUTIONS.get(QUALITY, (1920, 1080))
    try:
        assemble_video(
            scenes=valid_scenes,
            media_list=valid_media,
            audio_path=audio_path,
            output_path=OUTPUT_VIDEO,
            aspect_ratio=ASPECT,
            resolution=(width, height),
        )
        logger.info(f"✅ Video generated successfully: {OUTPUT_VIDEO}")
        logger.info(f"   File size: {OUTPUT_VIDEO.stat().st_size / (1024*1024):.1f} MB")
    except Exception as e:
        logger.exception("Video assembly failed!")
        sys.exit(1)
    finally:
        cleanup_temp_files(TEMP_DIR)

    logger.info("🏁 Pipeline completed. Upload artifact to get your video!")
    print(f"::set-output name=video_path::{OUTPUT_VIDEO}")

if __name__ == "__main__":
    main()
