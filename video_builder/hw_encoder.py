import shutil
import subprocess

_cached_config: dict | None = None


def get_encoder_config(ffmpeg_bin: str) -> dict:
    global _cached_config
    if _cached_config is not None:
        return _cached_config

    # Fall back to PATH lookup if the explicit binary doesn't exist
    import os
    if not os.path.isfile(ffmpeg_bin):
        ffmpeg_bin = shutil.which("ffmpeg") or ffmpeg_bin

    result = subprocess.run(
        [ffmpeg_bin, "-encoders"], capture_output=True, text=True
    )
    encoders = result.stdout

    if "h264_nvenc" in encoders:
        _cached_config = {
            "codec": "h264_nvenc",
            "ffmpeg_flags": ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "28"],
            "moviepy_kwargs": {"preset": "p4", "ffmpeg_params": ["-cq", "28"]},
        }
    elif "h264_videotoolbox" in encoders:
        _cached_config = {
            "codec": "h264_videotoolbox",
            "ffmpeg_flags": ["-c:v", "h264_videotoolbox", "-q:v", "65"],
            "moviepy_kwargs": {"ffmpeg_params": ["-q:v", "65"]},
        }
    else:
        _cached_config = {
            "codec": "libx264",
            "ffmpeg_flags": ["-c:v", "libx264", "-preset", "fast", "-crf", "28"],
            "moviepy_kwargs": {"preset": "fast", "ffmpeg_params": ["-crf", "28"]},
        }

    print(f"[encoder] Используем: {_cached_config['codec']}")
    return _cached_config
