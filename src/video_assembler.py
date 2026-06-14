"""
Video Assembler – uses MoviePy to combine media clips, apply visible transitions,
and Ken Burns effect on still images. Supports silent video.
"""

import logging, random
from pathlib import Path
from typing import List, Dict, Tuple, Optional

import PIL.Image
if not hasattr(PIL.Image, 'ANTIALIAS'):
    PIL.Image.ANTIALIAS = PIL.Image.LANCZOS

from moviepy.editor import (
    VideoFileClip,
    ImageClip,
    AudioFileClip,
    concatenate_videoclips,
    vfx,
)

logger = logging.getLogger(__name__)

TRANSITION_DURATION = 0.8
DEFAULT_SCENE_DURATION = 5.0  # seconds per scene when no audio

def _apply_in_transition(clip, duration=TRANSITION_DURATION):
    return clip.crossfadein(duration)

def _apply_out_transition(clip, duration=TRANSITION_DURATION):
    return clip.crossfadeout(duration)

def _ken_burns_effect(clip, duration, target_size):
    """
    Apply a slow zoom in/out or pan to an image clip to make it dynamic.
    Returns a new clip with the effect.
    """
    target_w, target_h = target_size
    # Make the clip slightly larger so we can zoom/pan
    scale_factor = 1.2
    big_clip = clip.resize(scale_factor)

    # Randomly choose zoom in, zoom out, or pan
    effect_type = random.choice(["zoom_in", "zoom_out", "pan_left", "pan_right"])

    def make_frame(t):
        # t goes from 0 to duration
        progress = t / duration
        if effect_type == "zoom_in":
            # Start at 1.2x, end at 1.0x (zoom out actually? we want to zoom in: make the image larger over time, so start with smaller scale and increase.
            # Better: start with scale such that image fills screen, then increase scale -> zoom in.
            # I'll implement start at 1.0 (full size) then zoom to 1.2 -> actually image gets bigger, so you lose edges, creating zoom in effect.
            # Simpler: just use a resize lambda.
            pass

    # MoviePy's resize can accept a function of time. We'll create a new clip with a time-dependent resize.
    if effect_type == "zoom_in":
        # Scale from 1.0 to 1.3 over duration
        def zoom_in_func(t):
            return 1.0 + 0.3 * (t / duration)
        clip_zoomed = clip.resize(zoom_in_func)
        # Then crop to target size (center)
        clip_zoomed = clip_zoomed.crop(x_center=clip_zoomed.w/2, y_center=clip_zoomed.h/2,
                                       width=target_w, height=target_h)
        return clip_zoomed.set_duration(duration)

    elif effect_type == "zoom_out":
        # Scale from 1.3 to 1.0
        def zoom_out_func(t):
            return 1.3 - 0.3 * (t / duration)
        clip_zoomed = clip.resize(zoom_out_func)
        clip_zoomed = clip_zoomed.crop(x_center=clip_zoomed.w/2, y_center=clip_zoomed.h/2,
                                       width=target_w, height=target_h)
        return clip_zoomed.set_duration(duration)

    elif effect_type == "pan_left":
        # Crop a moving window: start at right side, end at left
        big_w, big_h = big_clip.size
        start_x = big_w - target_w  # right edge
        end_x = 0
        def x_center_func(t):
            return start_x - (start_x - end_x) * (t / duration)
        cropped = big_clip.crop(x_center=x_center_func, y_center=big_h/2,
                                width=target_w, height=target_h)
        return cropped.set_duration(duration)

    elif effect_type == "pan_right":
        big_w, big_h = big_clip.size
        start_x = 0
        end_x = big_w - target_w
        def x_center_func(t):
            return start_x + (end_x - start_x) * (t / duration)
        cropped = big_clip.crop(x_center=x_center_func, y_center=big_h/2,
                                width=target_w, height=target_h)
        return cropped.set_duration(duration)

    # Fallback: static image with resize to fit
    clip_fit = clip.resize(newsize=(target_w, target_h))
    return clip_fit.set_duration(duration)


def _prepare_clip(media_item, duration, target_size):
    file_path = media_item["file_path"]
    media_type = media_item.get("type", "image")

    if media_type == "image":
        # Load image, apply Ken Burns effect
        img_clip = ImageClip(file_path)
        return _ken_burns_effect(img_clip, duration, target_size)
    else:
        clip = VideoFileClip(file_path)
        if clip.duration > duration:
            clip = clip.subclip(0, duration)
        elif clip.duration < duration:
            loops_needed = int(duration // clip.duration) + 1
            clip = concatenate_videoclips([clip] * loops_needed).subclip(0, duration)
        # Resize and crop to target
        target_w, target_h = target_size
        clip_w, clip_h = clip.size
        scale = max(target_w / clip_w, target_h / clip_h)
        clip = clip.resize(scale)
        clip = clip.crop(x_center=clip.w/2, y_center=clip.h/2,
                         width=target_w, height=target_h)
        return clip

def assemble_video(...):  # (same as before but with updated _prepare_clip)
