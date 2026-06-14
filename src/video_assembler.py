"""
Video Assembler – uses MoviePy to combine media clips, apply visible fade-to-black transitions,
and Ken Burns effect on still images (zoom in/out, pan left/right). Supports silent video.
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
    concatenate_videoclips,
)

logger = logging.getLogger(__name__)

TRANSITION_DURATION = 0.8           # seconds (clearly visible)
DEFAULT_SCENE_DURATION = 5.0        # seconds per scene when no audio


def _apply_in_transition(clip, duration=TRANSITION_DURATION):
    """Fade in from black at the beginning of a clip."""
    return clip.crossfadein(duration)


def _apply_out_transition(clip, duration=TRANSITION_DURATION):
    """Fade out to black at the end of a clip."""
    return clip.crossfadeout(duration)


def _ken_burns_effect(clip, duration, target_size):
    """
    Apply zoom in/out or pan left/right to an image clip.
    Works with MoviePy >=2.0 (supports callable in crop).
    """
    target_w, target_h = target_size
    effect_type = random.choice(["zoom_in", "zoom_out", "pan_left", "pan_right"])

    if effect_type == "zoom_in":
        def zoom_func(t):
            return 1.0 + 0.3 * (t / duration) if duration > 0 else 1.0
        clip_zoomed = clip.resize(zoom_func)
        clip_zoomed = clip_zoomed.crop(x_center=clip_zoomed.w/2, y_center=clip_zoomed.h/2,
                                       width=target_w, height=target_h)
        return clip_zoomed.set_duration(duration)

    elif effect_type == "zoom_out":
        def zoom_func(t):
            return 1.3 - 0.3 * (t / duration) if duration > 0 else 1.0
        clip_zoomed = clip.resize(zoom_func)
        clip_zoomed = clip_zoomed.crop(x_center=clip_zoomed.w/2, y_center=clip_zoomed.h/2,
                                       width=target_w, height=target_h)
        return clip_zoomed.set_duration(duration)

    elif effect_type == "pan_left":
        big_clip = clip.resize(1.2)
        big_w, big_h = big_clip.size
        start_x = big_w - target_w   # start at right edge
        end_x = 0                     # end at left edge
        def x_center_func(t):
            return start_x - (start_x - end_x) * (t / duration) if duration > 0 else start_x
        cropped = big_clip.crop(x_center=x_center_func, y_center=big_h/2,
                                width=target_w, height=target_h)
        return cropped.set_duration(duration)

    elif effect_type == "pan_right":
        big_clip = clip.resize(1.2)
        big_w, big_h = big_clip.size
        start_x = 0
        end_x = big_w - target_w
        def x_center_func(t):
            return start_x + (end_x - start_x) * (t / duration) if duration > 0 else start_x
        cropped = big_clip.crop(x_center=x_center_func, y_center=big_h/2,
                                width=target_w, height=target_h)
        return cropped.set_duration(duration)

    # Fallback (should never be reached)
    return clip.resize(newsize=(target_w, target_h)).set_duration(duration)


def _prepare_clip(
    media_item: Dict,
    duration: float,
    target_size: Tuple[int, int],
) -> VideoFileClip:
    """
    Load and prepare a media clip (image or video) to fit duration and size.
    For images, apply Ken Burns effect.
    For videos, trim/loop and resize/crop.
    """
    file_path = media_item["file_path"]
    media_type = media_item.get("type", "image")

    if media_type == "image":
        img_clip = ImageClip(file_path)
        return _ken_burns_effect(img_clip, duration, target_size)
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
        clip = clip.crop(x_center=clip.w/2, y_center=clip.h/2,
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
    """
    Main video assembly function.

    Args:
        scenes: text of each scene (not displayed)
        media_list: list of media dicts for each scene
        audio_path: path to voiceover MP3, or None for silent video
        output_path: destination MP4 file
        aspect_ratio: "16:9", "9:16", or "1:1"
        resolution: (width, height)
    """
    logger.info(f"Assembling video: {len(scenes)} scenes, {resolution[0]}x{resolution[1]}, {aspect_ratio}")

    if not media_list or len(media_list) == 0:
        raise ValueError("No media to assemble.")

    # Determine total duration
    if audio_path is not None:
        audio = AudioFileClip(str(audio_path))
        total_duration = audio.duration
        logger.info(f"Audio duration: {total_duration:.2f}s")
    else:
        audio = None
        total_duration = DEFAULT_SCENE_DURATION * len(media_list)
        logger.info(f"No audio – using {total_duration:.2f}s total ({DEFAULT_SCENE_DURATION}s per scene)")

    num_scenes = len(media_list)
    scene_duration = total_duration / num_scenes

    # Build clips
    clips = []
    for i, media in enumerate(media_list):
        clip = _prepare_clip(media, scene_duration, resolution)
        clips.append(clip)

    # Apply fade transitions: fade-in on all but first, fade-out on all but last
    for i in range(len(clips)):
        if i > 0:
            clips[i] = _apply_in_transition(clips[i])
        if i < len(clips) - 1:
            clips[i] = _apply_out_transition(clips[i])

    # Concatenate (method='compose' handles overlapping fade correctly)
    final_video = concatenate_videoclips(clips, method="compose")

    if audio is not None:
        final_video = final_video.set_audio(audio)

    # Write output
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

    # Cleanup
    for c in clips:
        c.close()
    if audio is not None:
        audio.close()
    final_video.close()
