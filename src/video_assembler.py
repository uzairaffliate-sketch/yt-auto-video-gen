"""
Video Assembler – uses MoviePy to combine media clips, apply visible fade-to-black transitions,
enforce aspect ratio & resolution, and sync with voiceover audio.
Supports silent video (no audio) by setting audio_path=None.
"""

import logging
import random
from pathlib import Path
from typing import List, Dict, Tuple, Optional

# Patch Pillow ANTIALIAS for newer versions
import PIL.Image
if not hasattr(PIL.Image, 'ANTIALIAS'):
    PIL.Image.ANTIALIAS = PIL.Image.LANCZOS

from moviepy.editor import (
    VideoFileClip,
    ImageClip,
    AudioFileClip,
    CompositeVideoClip,
    concatenate_videoclips,
    vfx,
)

logger = logging.getLogger(__name__)

# Transition duration (now clearly visible)
TRANSITION_DURATION = 0.8  # seconds

# Default scene duration when no audio is provided
DEFAULT_SCENE_DURATION = 5.0  # seconds


def _apply_in_transition(clip, duration: float = TRANSITION_DURATION):
    """Fade in from black at the beginning of a clip."""
    return clip.crossfadein(duration)

def _apply_out_transition(clip, duration: float = TRANSITION_DURATION):
    """Fade out to black at the end of a clip."""
    return clip.crossfadeout(duration)


def _prepare_clip(
    media_item: Dict,
    duration: float,
    target_size: Tuple[int, int],
) -> VideoFileClip:
    """
    Load and prepare a media clip (image or video) to fit duration and size.
    Transitions are applied separately by the assembler.
    """
    file_path = media_item["file_path"]
    media_type = media_item.get("type", "image")

    if media_type == "image":
        clip = ImageClip(file_path).set_duration(duration)
    else:
        clip = VideoFileClip(file_path)
        if clip.duration > duration:
            clip = clip.subclip(0, duration)
        elif clip.duration < duration:
            loops_needed = int(duration // clip.duration) + 1
            clip = concatenate_videoclips([clip] * loops_needed).subclip(0, duration)

    target_w, target_h = target_size
    clip_w, clip_h = clip.size
    scale = max(target_w / clip_w, target_h / clip_h)
    clip = clip.resize(scale)
    clip = clip.crop(x_center=clip.w / 2, y_center=clip.h / 2,
                     width=target_w, height=target_h)

    return clip


def assemble_video(
    scenes: List[str],
    media_list: List[Dict],
    audio_path: Optional[Path] = None,
    output_path: Path = None,
    aspect_ratio: str = "16:9",
    resolution: Tuple[int, int] = (1920, 1080),
) -> None:
    logger.info(f"Assembling video: {len(scenes)} scenes, {resolution[0]}x{resolution[1]}, {aspect_ratio}")

    if not media_list or len(media_list) == 0:
        raise ValueError("No media to assemble.")

    if audio_path is not None:
        audio = AudioFileClip(str(audio_path))
        total_duration = audio.duration
        logger.info(f"Audio duration: {total_duration:.2f}s")
    else:
        audio = None
        total_duration = DEFAULT_SCENE_DURATION * len(media_list)
        logger.info(f"No audio – using {total_duration:.2f}s total (5s per scene)")

    num_scenes = len(media_list)
    scene_duration = total_duration / num_scenes

    clips = []
    for i, media in enumerate(media_list):
        clip = _prepare_clip(media, scene_duration, resolution)
        clips.append(clip)

    # Apply fade-in to all clips except the first, and fade-out to all except the last
    for i in range(len(clips)):
        if i > 0:
            clips[i] = _apply_in_transition(clips[i])
        if i < len(clips) - 1:
            clips[i] = _apply_out_transition(clips[i])

    # Concatenate with compose to handle overlapping transitions correctly
    final_video = concatenate_videoclips(clips, method="compose")

    if audio is not None:
        final_video = final_video.set_audio(audio)

    logger.info(f"Rendering video to {output_path}...")
    final_video.write_videofile(
        str(output_path),
        fps=24,
        codec="libx264",
        audio_codec="aac",
        threads=4,
        preset="medium",
        ffmpeg_params=["-crf", "23"],
    )
    logger.info("Video rendering complete.")

    for c in clips:
        c.close()
    if audio is not None:
        audio.close()
    final_video.close()
