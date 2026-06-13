"""
Video Assembler – uses MoviePy to combine media clips, apply random transitions,
enforce aspect ratio & resolution, and sync with voiceover audio.
Supports silent video (no audio) by setting audio_path=None.
"""

import logging
import random
from pathlib import Path
from typing import List, Dict, Tuple, Optional

# ✅ Patch Pillow ANTIALIAS for newer versions (removed in Pillow 10+)
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
from moviepy.video.fx.resize import resize

logger = logging.getLogger(__name__)

# Available transition effects (randomly chosen between scenes)
TRANSITIONS = ["fade", "slide_left", "slide_right", "zoom_in", "crossfade"]

# Default transition duration in seconds
TRANSITION_DURATION = 0.5

# Default scene duration when no audio is provided
DEFAULT_SCENE_DURATION = 5.0  # seconds


def _apply_transition(clip, transition_name: str, duration: float = TRANSITION_DURATION):
    if transition_name == "fade":
        return clip.crossfadein(duration)
    elif transition_name == "slide_left":
        return clip.fx(vfx.slide_in, duration, "left")
    elif transition_name == "slide_right":
        return clip.fx(vfx.slide_in, duration, "right")
    elif transition_name == "zoom_in":
        return clip.fx(vfx.resize, lambda t: 1 + 0.1 * t)
    elif transition_name == "crossfade":
        return clip.crossfadein(duration)
    else:
        return clip


def _prepare_clip(
    media_item: Dict,
    duration: float,
    target_size: Tuple[int, int],
    transition: str = "fade",
    is_first: bool = False,
) -> VideoFileClip:
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

    if not is_first:
        clip = _apply_transition(clip, transition)

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
        if i == 0:
            transition = None
        else:
            transition = random.choice(TRANSITIONS)

        logger.debug(f"Scene {i+1}: media={media.get('file_path')}, duration={scene_duration:.2f}s, transition={transition}")
        clip = _prepare_clip(media, scene_duration, resolution,
                             transition=transition, is_first=(i == 0))
        clips.append(clip)

    clips_total_duration = sum(c.duration for c in clips)
    duration_diff = total_duration - clips_total_duration
    if duration_diff != 0:
        logger.info(f"Adjusting last clip duration by {duration_diff:.2f}s to match total duration.")
        last_clip = clips[-1]
        new_last = last_clip.set_duration(last_clip.duration + duration_diff)
        clips[-1] = new_last

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
