"""
Utility helpers for YT Auto Video Generator.
"""

import logging
import shutil
import sys
from pathlib import Path
from typing import Optional
import requests
from tqdm import tqdm  # progress bars on GitHub Actions logs


def setup_logging(level: int = logging.INFO) -> None:
    """Configure logging with timestamp and level for better traceability."""
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    # Reduce noise from other libraries
    logging.getLogger("PIL").setLevel(logging.WARNING)
    logging.getLogger("moviepy").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def ensure_output_dir(dir_path: Path) -> None:
    """Create directory if not exists. No error if exists."""
    dir_path.mkdir(parents=True, exist_ok=True)


def download_file(url: str, destination: Path, chunk_size: int = 8192) -> bool:
    """
    Download a file from a URL to local destination with progress bar.
    Returns True if successful, False otherwise.
    """
    logger = logging.getLogger(__name__)
    try:
        response = requests.get(url, stream=True, timeout=30)
        response.raise_for_status()

        total_size = int(response.headers.get("content-length", 0))
        with open(destination, "wb") as f:
            with tqdm(
                total=total_size,
                unit="B",
                unit_scale=True,
                desc=destination.name,
                leave=False,
            ) as pbar:
                for chunk in response.iter_content(chunk_size=chunk_size):
                    f.write(chunk)
                    pbar.update(len(chunk))
        return True
    except Exception as e:
        logger.error(f"Failed to download {url}: {e}")
        return False


def cleanup_temp_files(temp_dir: Path) -> None:
    """Remove all temporary media files after video assembly."""
    if temp_dir.exists():
        shutil.rmtree(temp_dir, ignore_errors=True)
        logging.getLogger(__name__).info(f"Cleaned up temporary directory: {temp_dir}")


def is_running_in_github_actions() -> bool:
    """Detect if the script is running inside GitHub Actions."""
    return "GITHUB_ACTIONS" in os.environ


def log_system_info() -> None:
    """Log basic system info for debugging."""
    import platform
    import os
    logger = logging.getLogger(__name__)
    logger.info(f"Python {platform.python_version()} | OS: {platform.system()} | CPU: {os.cpu_count()} cores")
    if is_running_in_github_actions():
        logger.info("Running in GitHub Actions environment")