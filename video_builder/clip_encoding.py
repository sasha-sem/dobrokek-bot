"""Encode source videos and photo memes for the episode manifest."""

from contextlib import contextmanager
import math
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import wave

from PIL import Image, ImageDraw, ImageFont
from tqdm import tqdm

from hw_encoder import ffmpeg_binary, get_encoder_config

os.environ.setdefault("FFMPEG_BINARY", ffmpeg_binary())
from moviepy import VideoFileClip  # noqa: E402


W, H, FPS = 1920, 1080, 30
VIDEO_FONT_PATH = str(Path(__file__).parent / "static/Roboto-Regular.ttf")
MEME_DURATION = 5
MEME_CAPTION_H = 140
AUDIO_FLAGS = ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]
SILENCE = "anullsrc=channel_layout=stereo:sample_rate=48000"


def draw_caption(text: str, font_path: str, font_size: int, size: tuple[int, int]) -> Image.Image:
    image = Image.new("RGBA", size)
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype(font_path, font_size)
    except OSError:
        tqdm.write(f"Не найден шрифт {font_path}. Используем шрифт по умолчанию.")
        font = ImageFont.load_default()

    width, height = size
    char_width = font.getlength("x") or font_size * 0.5
    wrapped = "\n".join(textwrap.wrap(text, width=max(1, int((width - 20) / char_width))))
    left, top, right, bottom = draw.multiline_textbbox((0, 0), wrapped, font=font, align="center")
    draw.multiline_text(
        ((width - right + left) // 2, (height - bottom + top) // 2),
        wrapped, font=font, fill="white", align="center",
    )
    return image


@contextmanager
def caption_input(text: str, font_path: str, font_size: int, size: tuple[int, int]):
    if not text.strip():
        yield []
        return
    with tempfile.TemporaryDirectory(prefix="dobrokek-caption-") as folder:
        path = Path(folder) / "caption.tga"
        draw_caption(text, font_path, font_size, size).save(path)
        yield ["-loop", "1", "-i", str(path)]


def process_video_fast(input_path: str, output_path: str, title: str, font_path: str = VIDEO_FONT_PATH,
                       w: int = W, h: int = H) -> None:
    if not Path(input_path).exists():
        raise FileNotFoundError(f"Файл не найден: {input_path}")

    with VideoFileClip(input_path) as clip:
        orig_w, orig_h = clip.size
        has_audio = clip.audio is not None
        duration = clip.duration

    new_w, new_h = int(orig_w * h / orig_h), h
    if new_w > w:
        new_w, new_h = w, int(orig_h * w / orig_w)
    new_w, new_h = new_w // 2 * 2, new_h // 2 * 2
    caption_w = (w - new_w) // 4 * 2
    caption = title if caption_w > 50 else ""
    ffmpeg = ffmpeg_binary()
    enc = get_encoder_config(ffmpeg)

    with caption_input(caption, font_path, 24, (caption_w, h)) as overlay:
        filters = (
            f"[0:v]scale={new_w}:{new_h}[vid];"
            f"color=c=black:s={w}x{h}[bg];"
            "[bg][vid]overlay=(W-w)/2:(H-h)/2:shortest=1"
        )
        if overlay:
            filters += "[bg_vid];[bg_vid][1:v]overlay=W-w:0:shortest=1"

        silence = [] if has_audio else ["-f", "lavfi", "-i", f"{SILENCE}:d={duration:.9f}"]
        audio_index = 0 if has_audio else 1 + bool(overlay)
        subprocess.run([
            ffmpeg, "-y", "-loglevel", "error", "-i", input_path, *overlay, *silence,
            "-filter_complex", f"{filters}[outv]", "-map", "[outv]", "-map", f"{audio_index}:a",
            *enc["ffmpeg_flags"], *AUDIO_FLAGS, "-r", str(FPS), "-shortest", output_path,
        ], check=True, capture_output=True, text=True)


def process_meme_image(input_path: str, output_path: str, title: str = "", font_path: str = VIDEO_FONT_PATH,
                       *, narration_path: Path | None = None) -> None:
    """Encode a photo for the length of the narration or five seconds of silence."""
    duration = MEME_DURATION
    audio_input = ["-f", "lavfi", "-i", SILENCE]
    if narration_path is not None:
        with wave.open(str(narration_path), "rb") as audio:
            duration = math.ceil(audio.getnframes() / audio.getframerate() * FPS) / FPS
        if duration <= 0:
            raise ValueError(f"Пустая озвучка: {narration_path}")
        audio_input = ["-i", str(narration_path)]

    ffmpeg = ffmpeg_binary()
    enc = get_encoder_config(ffmpeg)
    photo_h = H - MEME_CAPTION_H
    with caption_input(title, font_path, 28, (W, MEME_CAPTION_H)) as overlay:
        filters = (
            f"[0:v]scale={W}:{photo_h}:force_original_aspect_ratio=decrease,"
            f"pad={W}:{H}:(ow-iw)/2:({photo_h}-ih)/2:color=black"
        )
        if overlay:
            filters += f"[bg];[bg][1:v]overlay=0:{photo_h}"
        # Round the output of scale/pad down to the exact frame size.
        filters += f",crop={W}:{H}:0:0[outv]"
        audio_index = 1 + bool(overlay)
        subprocess.run([
            ffmpeg, "-y", "-loglevel", "error", "-loop", "1", "-i", input_path, *overlay, *audio_input,
            "-filter_complex", filters, "-map", "[outv]", "-map", f"{audio_index}:a",
            *enc["ffmpeg_flags"], *AUDIO_FLAGS, "-af", "apad", "-t", str(duration), "-r", str(FPS), output_path,
        ], check=True, capture_output=True, text=True)
