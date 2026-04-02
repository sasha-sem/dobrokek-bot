#!/usr/bin/env python3
import json
import multiprocessing
import os
import sys
from pathlib import Path

# --- НАСТРОЙКИ ОКРУЖЕНИЯ ---
wsl_native_path = "/usr/lib/wsl/lib"
if os.path.exists(wsl_native_path):
    os.environ["LD_LIBRARY_PATH"] = f"{wsl_native_path}:" + \
        os.environ.get("LD_LIBRARY_PATH", "")

os.environ["FFMPEG_BINARY"] = "/usr/local/bin/ffmpeg"
# ---------------------------

from moviepy import VideoFileClip, concatenate_videoclips  # noqa: E402

# ── Constants ─────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent

OUTPUT_FILE = BASE_DIR / "result/output.mp4"
ASSETS_DIR = BASE_DIR / "assets"
MANIFEST_PATH = BASE_DIR / "temp_clips/manifest.json"

# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> None:
    if not MANIFEST_PATH.exists():
        sys.exit("Ошибка: temp_clips/manifest.json не найден. Сначала запустите build_clips.py!")

    with open(MANIFEST_PATH, encoding="utf-8") as f:
        manifest = json.load(f)

    OUTPUT_FILE.parent.mkdir(exist_ok=True)

    clips = []

    intro_path = ASSETS_DIR / "intro.mp4"
    if intro_path.exists():
        print("-> Добавляем пререндер INTRO...")
        clips.append(VideoFileClip(str(intro_path)))
    else:
        print("ВНИМАНИЕ: assets/intro.mp4 не найдено. Сначала запустите build_assets.py!")

    for entry in manifest["clips"]:
        try:
            clips.append(VideoFileClip(entry["path"]))
        except Exception as e:
            print(f"[skip] {entry['path']}: {e}")

    outro_path = ASSETS_DIR / "outro.mp4"
    if outro_path.exists():
        print("-> Добавляем пререндер OUTRO...")
        clips.append(VideoFileClip(str(outro_path)))
    else:
        print("ВНИМАНИЕ: assets/outro.mp4 не найдено. Сначала запустите build_assets.py!")

    transition_path = ASSETS_DIR / "transition.mp4"
    if transition_path.exists():
        print("-> Используем пререндер TRANSITION...")
        transition = VideoFileClip(str(transition_path))
    else:
        print("ВНИМАНИЕ: assets/transition.mp4 не найдено. Сначала запустите build_assets.py!")
        transition = None

    print("-> Склеиваем финальное видео...")
    final = concatenate_videoclips(clips, method="chain", transition=transition)

    cores = multiprocessing.cpu_count()
    print(f"Запускаем финальный рендер в {cores} потоков...")

    final.write_videofile(
        str(OUTPUT_FILE),
        fps=30,
        codec="h264_nvenc",
        audio_codec="aac",
        threads=cores,
        preset="p4",
        ffmpeg_params=["-cq", "28"],
    )


if __name__ == "__main__":
    main()
