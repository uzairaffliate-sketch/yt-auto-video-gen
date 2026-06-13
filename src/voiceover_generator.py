"""
Voiceover generator – creates audio from text (TTS) or downloads custom audio.
Uses gTTS (Google Text‑to‑Speech) for zero‑cost, natural sounding output.
"""

import logging
import tempfile
from pathlib import Path
from typing import Optional

from pydub import AudioSegment

from utils import download_file

logger = logging.getLogger(__name__)

# gTTS may split long text into chunks automatically, but we chunk manually to
# avoid internal length limits and allow progress feedback.
MAX_CHUNK_LENGTH = 2000  # characters per TTS chunk (safe for gTTS)


def _tts_chunk(text: str, output_file: Path) -> bool:
    """Generate a single TTS MP3 chunk using gTTS."""
    from gtts import gTTS
    try:
        tts = gTTS(text=text, lang="en", slow=False)
        tts.save(str(output_file))
        return True
    except Exception as e:
        logger.error(f"TTS chunk failed: {e}")
        return False


def _generate_tts(text: str, output_path: Path) -> None:
    """Generate a full voiceover from text, handling long scripts."""
    if not text.strip():
        raise ValueError("TTS text is empty.")

    logger.info(f"Generating TTS for {len(text)} characters...")

    # Split text into manageable chunks
    chunks = []
    remaining = text
    while len(remaining) > MAX_CHUNK_LENGTH:
        # Try to split at sentence boundary near the limit
        split_at = remaining.rfind(".", 0, MAX_CHUNK_LENGTH)
        if split_at == -1:
            split_at = MAX_CHUNK_LENGTH
        else:
            split_at += 1  # include the period
        chunk = remaining[:split_at].strip()
        if chunk:
            chunks.append(chunk)
        remaining = remaining[split_at:].strip()
    if remaining:
        chunks.append(remaining)

    logger.info(f"Script split into {len(chunks)} TTS chunk(s)")

    # Generate each chunk
    chunk_files = []
    with tempfile.TemporaryDirectory() as tmpdirname:
        tmp = Path(tmpdirname)
        for i, chunk in enumerate(chunks):
            chunk_file = tmp / f"chunk_{i}.mp3"
            logger.info(f"Processing chunk {i+1}/{len(chunks)} ({len(chunk)} chars)")
            if not _tts_chunk(chunk, chunk_file):
                raise RuntimeError(f"Failed to generate TTS chunk {i}.")
            chunk_files.append(chunk_file)

        # Combine chunks
        combined = AudioSegment.empty()
        for cf in chunk_files:
            segment = AudioSegment.from_mp3(cf)
            combined += segment

        # Export final file
        combined.export(output_path, format="mp3")
        logger.info(f"TTS voiceover saved to {output_path} "
                    f"({len(combined)/1000:.1f} s)")


def _process_custom_audio(audio_url: str, output_path: Path) -> None:
    """Download a custom audio file from the given URL."""
    logger.info(f"Downloading custom audio from {audio_url}")
    success = download_file(audio_url, output_path)
    if not success:
        raise RuntimeError("Failed to download custom audio file.")
    # Ensure it's a supported format (pydub can handle many)
    try:
        AudioSegment.from_file(output_path)
    except Exception:
        raise ValueError("Downloaded audio file is not a valid audio format.")
    logger.info(f"Custom audio saved to {output_path}")


def generate_audio(mode: str, text: str = "", audio_url: str = "",
                   output_path: Path = Path("temp_media/voiceover.mp3")) -> None:
    """
    Entry point for audio generation.

    Args:
        mode: "tts" or "custom_audio"
        text: full script text (only for tts mode)
        audio_url: URL of the audio file (only for custom_audio mode)
        output_path: where to save the resulting MP3 file
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if mode == "tts":
        if not text:
            raise ValueError("TTS mode requires 'text' parameter.")
        _generate_tts(text, output_path)
    elif mode == "custom_audio":
        if not audio_url:
            raise ValueError("Custom audio mode requires 'audio_url' parameter.")
        _process_custom_audio(audio_url, output_path)
    else:
        raise ValueError(f"Unknown voiceover mode: {mode}")