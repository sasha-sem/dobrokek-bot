#!/usr/bin/env python3
"""
Encodes all raw clips (first, main loop, last) into temp_clips/ via FFmpeg NVENC.
Writes temp_clips/manifest.json when done.
Run BEFORE build_video.py.
"""
import json
import os
import random
import re
import shutil
import subprocess
import textwrap
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from tqdm import tqdm

# --- НАСТРОЙКИ ОКРУЖЕНИЯ ---
wsl_native_path = "/usr/lib/wsl/lib"
if os.path.exists(wsl_native_path):
    os.environ["LD_LIBRARY_PATH"] = f"{wsl_native_path}:" + \
        os.environ.get("LD_LIBRARY_PATH", "")

os.environ["FFMPEG_BINARY"] = "/usr/local/bin/ffmpeg"
# ---------------------------

from moviepy import VideoFileClip  # noqa: E402  (must be after env setup)

# ── Constants ─────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent

INPUT_FILE = BASE_DIR / "export/result.json"
INPUT_DIR = BASE_DIR / "export"
TEMP_DIR = BASE_DIR / "temp_clips"
MANIFEST_PATH = TEMP_DIR / "manifest.json"

W, H = 1920, 1080
VIDEO_FONT_PATH = str(BASE_DIR / "static/Roboto-Regular.ttf")

FIRST_CLIP_PATH = str(BASE_DIR / "export/first_clip.MP4")
LAST_CLIP_PATH = str(BASE_DIR / "export/last_clip.mp4")

# ── Helpers ───────────────────────────────────────────────────────────────────


def demoji(text: str) -> str:
    """Удаляет эмодзи из строки."""
    emoji_pattern = re.compile(
        "["
        "\U0001F600-\U0001F64F"
        "\U0001F300-\U0001F5FF"
        "\U0001F680-\U0001F6FF"
        "\U0001F1E0-\U0001F1FF"
        "]+",
        flags=re.UNICODE,
    )
    return emoji_pattern.sub(r"", text)


def draw_text_pillow(text: str, font_path: str, font_size: int, image_size: tuple[int, int]) -> Image.Image:
    """Рисует текст с переносом строк на прозрачном фоне TGA."""
    img = Image.new("RGBA", image_size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    try:
        font = ImageFont.truetype(font_path, font_size)
    except IOError:
        tqdm.write(f"Ошибка: Не найден шрифт по пути {font_path}. Рисуем дефолтным.")
        font = ImageFont.load_default()

    width, height = image_size

    if hasattr(font, "getlength"):
        avg_char_width = font.getlength("x")
    else:
        avg_char_width = font.getsize("x")[0]

    if avg_char_width == 0:
        avg_char_width = font_size * 0.5

    max_char_count = max(1, int((width - 20) / avg_char_width))
    lines = textwrap.wrap(text, width=max_char_count)
    wrapped_text = "\n".join(lines)

    if hasattr(draw, "multiline_textbbox"):
        bbox = draw.multiline_textbbox((0, 0), wrapped_text, font=font, align="center")
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
    else:
        text_w, text_h = draw.multiline_textsize(wrapped_text, font=font)

    draw.multiline_text(
        ((width - text_w) // 2, (height - text_h) // 2),
        wrapped_text,
        font=font,
        fill=(255, 255, 255, 255),
        align="center",
        spacing=4,
    )
    return img


def process_video_fast(input_path: str, output_path: str, title: str, font_path: str, w: int = 1920, h: int = 1080) -> None:
    """Быстрая обработка видео через FFmpeg (NVENC + PIL TGA Text)."""
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"Файл не найден: {input_path}")

    clip = VideoFileClip(input_path)
    orig_w, orig_h = clip.w, clip.h
    clip.close()

    new_h = h
    new_w = int(orig_w * (h / orig_h))

    if new_w > w:
        new_w = w
        new_h = int(orig_h * (w / orig_w))

    new_w = new_w if new_w % 2 == 0 else new_w - 1
    new_h = new_h if new_h % 2 == 0 else new_h - 1

    caption_w = (w - new_w) // 2
    caption_w = caption_w if caption_w % 2 == 0 else caption_w - 1

    has_text = caption_w > 50 and bool(title.strip())
    temp_text_img = f"temp_caption_{Path(input_path).stem}.tga"

    if has_text:
        pil_image = draw_text_pillow(title, font_path, font_size=24, image_size=(caption_w, h))
        pil_image.save(temp_text_img)

    ffmpeg_bin = os.environ.get("FFMPEG_BINARY", "ffmpeg")

    if has_text:
        filter_complex = (
            f"[0:v]scale={new_w}:{new_h}[vid];"
            f"color=c=black:s={w}x{h}[bg];"
            f"[bg][vid]overlay=(W-w)/2:(H-h)/2:shortest=1[bg_vid];"
            f"[bg_vid][1:v]overlay=W-w:0:shortest=1[outv]"
        )
        cmd = [
            ffmpeg_bin, "-y",
            "-i", input_path,
            "-loop", "1", "-i", temp_text_img,
            "-filter_complex", filter_complex,
            "-map", "[outv]", "-map", "0:a?",
            "-c:v", "h264_nvenc", "-preset", "p4", "-cq", "28", "-c:a", "aac", "-shortest",
            output_path,
        ]
    else:
        filter_complex = (
            f"[0:v]scale={new_w}:{new_h}[vid];"
            f"color=c=black:s={w}x{h}[bg];"
            f"[bg][vid]overlay=(W-w)/2:(H-h)/2:shortest=1[outv]"
        )
        cmd = [
            ffmpeg_bin, "-y",
            "-i", input_path,
            "-filter_complex", filter_complex,
            "-map", "[outv]", "-map", "0:a?",
            "-c:v", "h264_nvenc", "-preset", "p4", "-cq", "28", "-c:a", "aac", "-shortest",
            output_path,
        ]

    try:
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except subprocess.CalledProcessError as e:
        tqdm.write(f"\n[!!!] ОШИБКА FFMPEG при обработке {Path(input_path).name}:\n{e.stderr}")
        raise
    finally:
        if os.path.exists(temp_text_img):
            os.remove(temp_text_img)


def load_videos() -> list[dict]:
    """Читает export/result.json, возвращает перемешанный список {filepath, title}."""
    with open(INPUT_FILE, encoding="utf-8") as f:
        export_dict = json.load(f)

    videos: list[dict] = []
    for message in random.sample(export_dict["messages"], k=len(export_dict["messages"])):
        if message.get("media_type") != "video_file":
            continue
        title, skip = "", False
        for entity in message["text_entities"]:
            if entity["type"] == "hashtag" and entity["text"] == "#dobrokek":
                skip = True
                break
            elif entity["type"] in ("plain", "code"):
                title += entity["text"].replace("\n\n", " ").strip() + " "
        if skip:
            continue
        videos.append({
            "filepath": str(INPUT_DIR / message["file"]),
            "title": demoji(title).strip(),
        })
    return videos


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> None:
    if TEMP_DIR.exists():
        shutil.rmtree(TEMP_DIR)
    TEMP_DIR.mkdir(exist_ok=True)

    videos = load_videos()

    # Строим полный список работы: first + основные клипы + last
    work: list[tuple[str, str, str, str]] = []  # (input, output, title, role)
    if os.path.exists(FIRST_CLIP_PATH):
        work.append((FIRST_CLIP_PATH, str(TEMP_DIR / "processed_first.mp4"), "", "first"))
    for i, v in enumerate(videos):
        work.append((v["filepath"], str(TEMP_DIR / f"processed_{i}.mp4"), v["title"], "main"))
    if os.path.exists(LAST_CLIP_PATH):
        work.append((LAST_CLIP_PATH, str(TEMP_DIR / "processed_last.mp4"), "", "last"))

    manifest_clips: list[dict] = []
    manifest_skipped: list[dict] = []
    elapsed_times: list[float] = []
    total = len(work)

    bar = tqdm(work, desc="Encoding clips", unit="clip", dynamic_ncols=True)

    for input_path, output_path, title, role in bar:
        bar.set_postfix_str(f"{Path(input_path).name[:35]}", refresh=True)
        t0 = time.perf_counter()
        try:
            process_video_fast(input_path, output_path, title, VIDEO_FONT_PATH, W, H)
            elapsed = time.perf_counter() - t0
            elapsed_times.append(elapsed)

            avg = sum(elapsed_times) / len(elapsed_times)
            remaining = total - bar.n
            eta_sec = avg * remaining

            bar.set_postfix(
                last=f"{elapsed:.1f}s",
                avg=f"{avg:.1f}s",
                eta=f"{eta_sec / 60:.1f}m",
                refresh=True,
            )
            manifest_clips.append({"role": role, "path": str(output_path), "title": title})
        except Exception as e:
            elapsed_times.append(time.perf_counter() - t0)
            tqdm.write(f"[skip] {Path(input_path).name}: {e}")
            manifest_skipped.append({"filepath": input_path, "error": str(e)})

    MANIFEST_PATH.write_text(
        json.dumps({"clips": manifest_clips, "skipped": manifest_skipped}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tqdm.write(f"\nManifest: {MANIFEST_PATH}")
    tqdm.write(f"Готово: {len(manifest_clips)} клипов закодировано, {len(manifest_skipped)} пропущено.")


if __name__ == "__main__":
    main()
