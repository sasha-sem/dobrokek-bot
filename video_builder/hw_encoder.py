from functools import cache
import os
from pathlib import Path
import shutil
import subprocess


WSL_LIB_DIR = "/usr/lib/wsl/lib"


def ffmpeg_binary() -> str:
    if Path(WSL_LIB_DIR).is_dir():
        library_paths = os.environ.get("LD_LIBRARY_PATH", "").split(os.pathsep)
        if WSL_LIB_DIR not in library_paths:
            os.environ["LD_LIBRARY_PATH"] = os.pathsep.join([WSL_LIB_DIR, *filter(None, library_paths)])
    return os.environ.get("FFMPEG_BINARY") or shutil.which("ffmpeg") or "/usr/local/bin/ffmpeg"


@cache
def get_encoder_config(ffmpeg_bin: str) -> dict:
    result = subprocess.run(
        [ffmpeg_bin, "-encoders"], check=True, capture_output=True, text=True
    )
    encoders = result.stdout

    if "h264_nvenc" in encoders:
        config = {
            "codec": "h264_nvenc",
            "ffmpeg_flags": ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "19", "-pix_fmt", "yuv420p"],
            "moviepy_kwargs": {"preset": "p4", "ffmpeg_params": ["-cq", "19", "-pix_fmt", "yuv420p"]},
        }
    elif "h264_videotoolbox" in encoders:
        config = {
            "codec": "h264_videotoolbox",
            "ffmpeg_flags": ["-c:v", "h264_videotoolbox", "-q:v", "80", "-pix_fmt", "yuv420p"],
            "moviepy_kwargs": {"ffmpeg_params": ["-q:v", "80", "-pix_fmt", "yuv420p"]},
        }
    else:
        config = {
            "codec": "libx264",
            "ffmpeg_flags": ["-c:v", "libx264", "-preset", "fast", "-crf", "19", "-pix_fmt", "yuv420p"],
            "moviepy_kwargs": {"preset": "fast", "ffmpeg_params": ["-crf", "19", "-pix_fmt", "yuv420p"]},
        }

    print(f"[encoder] Используем: {config['codec']}")
    return config
