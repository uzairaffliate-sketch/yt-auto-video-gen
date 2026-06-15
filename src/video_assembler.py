"""
Video Assembler – uses MoviePy 2.x to combine media clips, apply visible
fade-to-black transitions, and a gentle cinematic Ken Burns effect on still
images (subtle zoom in/out, pan left/right/up/down).
Supports silent video.
"""

import logging
import random
from pathlib import Path
from typing import List, Dict, Tuple, Optional

import numpy as np

# Patch Pillow ANTIALIAS for newer versions (some deps still reference it)
import PIL.Image
if not hasattr(PIL.Image, 'ANTIALIAS'):
    PIL.Image.ANTIALIAS = PIL.Image.LANCZOS

# MoviePy 2.x: import directly from `moviepy` (no `.editor`)
from moviepy import (
    VideoFileClip,
    ImageClip,
    AudioFileClip,
    concatenate_videoclips,
)
from moviepy.video.fx import CrossFadeIn, CrossFadeOut, Resize, Crop

logger = logging.getLogger(__name__)

TRANSITION_DURATION = 0.8           # seconds (clearly visible)
DEFAULT_SCENE_DURATION = 5.0        # seconds per scene when no audio


def _apply_in_transition(clip, duration=TRANSITION_DURATION):
    """Fade in from black at the beginning of a clip."""
    return clip.with_effects([CrossFadeIn(duration)])


def _apply_out_transition(clip, duration=TRANSITION_DURATION):
    """Fade out to black at the end of a clip."""
    return clip.with_effects([CrossFadeOut(duration)])


def _ken_burns_effect(clip, duration, target_size):
    """
    Smooth, subtle Ken Burns effect for MoviePy 2.x.

    Key idea to avoid jitter/shutter:
      * Upscale the image ONCE to a large base (no per-frame resize of source).
      * Every frame we read a window from that base whose size changes
        smoothly (for zoom) and/or whose position changes smoothly (for pan),
        then resize that window to the EXACT target size with PIL/LANCZOS.
        Because the output size is constant every frame, there is no abrupt
        pixel snapping / shutter.
    """
    target_w, target_h = target_size
    clip = clip.with_duration(duration)

    ZOOM = 0.08          # very subtle 8% zoom across the whole clip
    PAN_FRACTION = 0.10  # pan across at most 10% of the image

    # Base must be big enough for both max zoom AND pan headroom.
    base_scale = 1.0 + ZOOM + PAN_FRACTION
    base = clip.with_effects([
        Resize(new_size=(int(target_w * base_scale), int(target_h * base_scale)))
    ])

    effect_type = random.choice([
        "zoom_in", "zoom_out",
        "pan_left", "pan_right", "pan_up", "pan_down",
    ])

    def ease(p):
        # smoothstep: gentle start and stop
        return p * p * (3 - 2 * p)

    def make_frame(get_frame, t):
        frame = get_frame(t)                       # full big frame (constant size)
        fh, fw = frame.shape[:2]
        p = ease(t / duration) if duration > 0 else 0.0

        # --- decide zoom level (window size as fraction of target) ---
        if effect_type == "zoom_in":
            win_scale = 1.0 - ZOOM * p             # window shrinks -> looks like zoom in
        elif effect_type == "zoom_out":
            win_scale = (1.0 - ZOOM) + ZOOM * p    # window grows -> zoom out
        else:
            win_scale = 1.0 - ZOOM * 0.5           # constant mild crop for pans

        win_w = int(target_w * win_scale)
        win_h = int(target_h * win_scale)
        win_w = min(win_w, fw)
        win_h = min(win_h, fh)

        # --- decide window center (pan) ---
        max_x = fw - win_w
        max_y = fh - win_h
        cx = fw / 2.0
        cy = fh / 2.0
        if effect_type == "pan_left":
            cx = (max_x - max_x * p) + win_w / 2.0
        elif effect_type == "pan_right":
            cx = (max_x * p) + win_w / 2.0
        elif effect_type == "pan_up":
            cy = (max_y - max_y * p) + win_h / 2.0
        elif effect_type == "pan_down":
            cy = (max_y * p) + win_h / 2.0

        x1 = int(round(cx - win_w / 2.0))
        y1 = int(round(cy - win_h / 2.0))
        x1 = max(0, min(x1, fw - win_w))
        y1 = max(0, min(y1, fh - win_h))

        window = frame[y1:y1 + win_h, x1:x1 + win_w]

        # Resize this window to the EXACT target size so output is constant.
        pil_img = PIL.Image.fromarray(window).resize((target_w, target_h), PIL.Image.LANCZOS)
        return np.asarray(pil_img)

    moving = base.transform(make_frame, apply_to=[])
    return moving.with_duration(duration)


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
            clip = clip.subclipped(0, duration)
        elif clip.duration < duration:
            loops_needed = int(duration // clip.duration) + 1
            clip = concatenate_videoclips([clip] * loops_needed).subclipped(0, duration)

        target_w, target_h = target_size
        clip_w, clip_h = clip.size
        scale = max(target_w / clip_w, target_h / clip_h)
        clip = clip.with_effects([Resize(scale)])
        clip = clip.with_effects([
            Crop(x_center=clip.w / 2, y_center=clip.h / 2,
                 width=target_w, height=target_h)
        ])
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
        final_video = final_video.with_audio(audio)

    # Write output
    logger.info(f"Rendering video to {output_path}...")
    final_video.write_videofile(
        str(output_path),
        fps=30,
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
