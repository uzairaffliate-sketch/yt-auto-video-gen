"""
Video Assembler – uses MoviePy 2.x to combine media clips, apply visible
fade-to-black transitions, and Ken Burns effect on still images
(zoom in/out, pan left/right). Supports silent video.
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


def _crop_frame(frame, x_center, y_center, w, h):
    """Crop a (H, W, 3) numpy frame around the given center, clamped to bounds."""
    fh, fw = frame.shape[:2]
    w = min(int(w), fw)
    h = min(int(h), fh)
    x1 = int(round(x_center - w / 2))
    y1 = int(round(y_center - h / 2))
    # Clamp so we never read outside the frame
    x1 = max(0, min(x1, fw - w))
    y1 = max(0, min(y1, fh - h))
    return frame[y1:y1 + h, x1:x1 + w]


def _ken_burns_effect(clip, duration, target_size):
    """
    Apply zoom in/out or pan left/right to an image clip (MoviePy 2.x).

    MoviePy 2.x Crop/Resize effects do NOT accept time-varying callables,
    so the motion is implemented manually with clip.transform(), which
    yields a fresh, correctly-sized frame for every timestamp t.
    """
    target_w, target_h = target_size
    clip = clip.with_duration(duration)
    effect_type = random.choice(["zoom_in", "zoom_out", "pan_left", "pan_right"])

    if effect_type in ("zoom_in", "zoom_out"):
        # Upscale so the most-zoomed-in frame still fully covers the target.
        max_zoom = 1.3
        base = clip.with_effects([
            Resize(new_size=(int(target_w * max_zoom), int(target_h * max_zoom)))
        ])
        bw, bh = base.size

        def make_frame_zoom(get_frame, t):
            frame = get_frame(t)
            progress = (t / duration) if duration > 0 else 0.0
            if effect_type == "zoom_in":
                scale = 1.0 + 0.3 * progress      # 1.0 -> 1.3
            else:
                scale = 1.3 - 0.3 * progress      # 1.3 -> 1.0
            crop_w = min(int(target_w * max_zoom / scale), bw)
            crop_h = min(int(target_h * max_zoom / scale), bh)
            cropped = _crop_frame(frame, bw / 2, bh / 2, crop_w, crop_h)
            return cropped

        moving = base.transform(make_frame_zoom, apply_to=[])
        # Normalize every frame back to the exact target size.
        return moving.with_effects([
            Resize(new_size=(target_w, target_h))
        ]).with_duration(duration)

    else:
        # Pan: upscale slightly, then slide the crop window horizontally.
        base = clip.with_effects([Resize(1.25)])
        bw, bh = base.size
        max_x = max(0, bw - target_w)

        def make_frame_pan(get_frame, t):
            frame = get_frame(t)
            progress = (t / duration) if duration > 0 else 0.0
            if effect_type == "pan_left":
                # start at right edge, move to left
                x_center = (max_x - max_x * progress) + target_w / 2
            else:  # pan_right
                x_center = (max_x * progress) + target_w / 2
            return _crop_frame(frame, x_center, bh / 2, target_w, target_h)

        moving = base.transform(make_frame_pan, apply_to=[])
        # Guarantee exact output size even if rounding shaved a pixel.
        return moving.with_effects([
            Resize(new_size=(target_w, target_h))
        ]).with_duration(duration)


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
