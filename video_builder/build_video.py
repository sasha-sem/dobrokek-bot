#!/usr/bin/env python3
import json
import multiprocessing
import os
import sys
from pathlib import Path

# --- НАСТРОЙКИ ОКРУЖЕНИЯ ---
import shutil
wsl_native_path = "/usr/lib/wsl/lib"
if os.path.exists(wsl_native_path):
    os.environ["LD_LIBRARY_PATH"] = f"{wsl_native_path}:" + \
        os.environ.get("LD_LIBRARY_PATH", "")

os.environ["FFMPEG_BINARY"] = shutil.which("ffmpeg") or "/usr/local/bin/ffmpeg"
# ---------------------------

from moviepy import VideoFileClip, concatenate_videoclips  # noqa: E402

from hw_encoder import get_encoder_config

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

    # Разделяем клипы по ролям
    before_memes: list = []
    meme_clips_list: list = []
    after_memes: list = []
    in_memes = False

    for entry in manifest["clips"]:
        try:
            clip = VideoFileClip(entry["path"])
        except Exception as e:
            print(f"[skip] {entry['path']}: {e}")
            continue
        if entry["role"] == "meme":
            in_memes = True
            meme_clips_list.append(clip)
        elif in_memes:
            after_memes.append(clip)
        else:
            before_memes.append(clip)

    # Собираем секцию мемов: заставка -> мемы -> статистика «Герои мемной паузы»
    meme_section = None
    meme_pause_path = ASSETS_DIR / "meme_pause.mp4"
    meme_heroes_path = ASSETS_DIR / "meme_heroes.mp4"
    if meme_clips_list:
        if meme_pause_path.exists():
            head = [VideoFileClip(str(meme_pause_path))]
            if meme_heroes_path.exists():
                tail = [VideoFileClip(str(meme_heroes_path))]
            else:
                print("ВНИМАНИЕ: assets/meme_heroes.mp4 не найдено. Используем заставку МЕМНОЙ ПАУЗЫ в конце. Запустите build_assets.py!")
                tail = [VideoFileClip(str(meme_pause_path))]
            meme_section = concatenate_videoclips(head + meme_clips_list + tail, method="chain")
            print(f"-> Добавляем МЕМНУЮ ПАУЗУ ({len(meme_clips_list)} мемов)...")
        else:
            print("ВНИМАНИЕ: assets/meme_pause.mp4 не найдено. Мемная пауза пропущена. Запустите build_assets.py!")

    clips = []

    intro_path = ASSETS_DIR / "intro.mp4"
    if intro_path.exists():
        print("-> Добавляем пререндер INTRO...")
        clips.append(VideoFileClip(str(intro_path)))
    else:
        print("ВНИМАНИЕ: assets/intro.mp4 не найдено. Сначала запустите build_assets.py!")

    clips += before_memes
    if meme_section is not None:
        clips.append(meme_section)
    clips += after_memes

    outro_path = ASSETS_DIR / "outro.mp4"
    if outro_path.exists():
        print("-> Добавляем пререндер OUTRO...")
        clips.append(VideoFileClip(str(outro_path)))
    else:
        print("ВНИМАНИЕ: assets/outro.mp4 не найдено. Сначала запустите build_assets.py!")

    print("-> Склеиваем финальное видео...")
    final = concatenate_videoclips(clips, method="chain")

    cores = multiprocessing.cpu_count()
    print(f"Запускаем финальный рендер в {cores} потоков...")

    ffmpeg_bin = os.environ.get("FFMPEG_BINARY", "ffmpeg")
    enc = get_encoder_config(ffmpeg_bin)

    final.write_videofile(
        str(OUTPUT_FILE),
        fps=30,
        codec=enc["codec"],
        audio_codec="aac",
        threads=cores,
        **enc["moviepy_kwargs"]
    )


if __name__ == "__main__":
    main()
