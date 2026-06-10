#!/usr/bin/env python3
import json
import os
import subprocess
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

from hw_encoder import get_encoder_config  # noqa: E402

# ── Constants ─────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent

OUTPUT_FILE = BASE_DIR / "result/output.mp4"
ASSETS_DIR = BASE_DIR / "assets"
TEMP_DIR = BASE_DIR / "temp_clips"
MANIFEST_PATH = TEMP_DIR / "manifest.json"
CONCAT_LIST_PATH = TEMP_DIR / "concat_list.txt"

# Канонические параметры сегмента (как у клипов из build_clips.py).
# Сегменты, которые им соответствуют, склеиваются без перекодирования (lossless).
# Несоответствующие (например, кастомные ассеты) нормализуются перекодированием.
W, H, FPS = 1920, 1080, 30


def _ffprobe(ffprobe_bin: str, path: str, stream: str, entries: str) -> list[str]:
    """Возвращает запрошенные поля первого потока stream ('v:0'/'a:0') или []."""
    res = subprocess.run(
        [ffprobe_bin, "-v", "error", "-select_streams", stream,
         "-show_entries", f"stream={entries}", "-of", "default=nk=1:nw=1", path],
        capture_output=True, text=True,
    )
    return res.stdout.split()


def prepare_ts(ffmpeg_bin: str, ffprobe_bin: str, src: str, ts_path: Path, enc: dict) -> None:
    """Ремуксит сегмент в MPEG-TS для чистой склейки.

    Видео/аудио, уже совпадающие с каноном (h264 1920x1080 / aac 48к стерео),
    копируются без потерь. Несоответствующие (кастомные ассеты) приводятся
    к канону перекодированием, а отсутствующая аудиодорожка заменяется тишиной.
    """
    v = _ffprobe(ffprobe_bin, src, "v:0", "codec_name,width,height")
    a = _ffprobe(ffprobe_bin, src, "a:0", "codec_name,sample_rate,channels")

    video_ok = len(v) == 3 and v[0] == "h264" and v[1] == str(W) and v[2] == str(H)
    has_audio = len(a) >= 1
    audio_ok = len(a) == 3 and a[0] == "aac" and a[1] == "48000" and a[2] == "2"

    cmd = [ffmpeg_bin, "-y", "-loglevel", "error", "-i", src]
    if not has_audio:
        cmd += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]

    # видео
    cmd += ["-map", "0:v:0"]
    if video_ok:
        cmd += ["-c:v", "copy", "-bsf:v", "h264_mp4toannexb"]
    else:
        print(f"   [normalize] {Path(src).name}: видео не канон ({'/'.join(v) or 'нет'}) -> перекодируем")
        vf = (f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
              f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1")
        cmd += ["-vf", vf, "-r", str(FPS), *enc["ffmpeg_flags"]]

    # аудио
    if has_audio:
        cmd += ["-map", "0:a:0"]
        if audio_ok:
            cmd += ["-c:a", "copy"]
        else:
            print(f"   [normalize] {Path(src).name}: аудио не канон ({'/'.join(a)}) -> перекодируем")
            cmd += ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]
    else:
        print(f"   [normalize] {Path(src).name}: нет аудио -> добавляем тишину")
        cmd += ["-map", "1:a:0", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", "-shortest"]

    cmd += ["-f", "mpegts", str(ts_path)]
    subprocess.run(cmd, check=True)


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> None:
    if not MANIFEST_PATH.exists():
        sys.exit("Ошибка: temp_clips/manifest.json не найден. Сначала запустите build_clips.py!")

    with open(MANIFEST_PATH, encoding="utf-8") as f:
        manifest = json.load(f)

    OUTPUT_FILE.parent.mkdir(exist_ok=True)

    # Разделяем клипы по ролям (порядок внутри ролей сохраняем как в манифесте)
    before_memes: list[str] = []
    meme_clips_list: list[str] = []
    after_memes: list[str] = []
    in_memes = False

    for entry in manifest["clips"]:
        path = entry["path"]
        if not os.path.exists(path):
            print(f"[skip] {path}: файл не найден")
            continue
        if entry["role"] == "meme":
            in_memes = True
            meme_clips_list.append(path)
        elif in_memes:
            after_memes.append(path)
        else:
            before_memes.append(path)

    # Секция мемов: заставка -> мемы -> статистика «Герои мемной паузы»
    meme_section: list[str] = []
    meme_pause_path = ASSETS_DIR / "meme_pause.mp4"
    meme_heroes_path = ASSETS_DIR / "meme_heroes.mp4"
    if meme_clips_list:
        if meme_pause_path.exists():
            meme_section.append(str(meme_pause_path))
            meme_section += meme_clips_list
            if meme_heroes_path.exists():
                meme_section.append(str(meme_heroes_path))
            else:
                print("ВНИМАНИЕ: assets/meme_heroes.mp4 не найдено. Используем заставку МЕМНОЙ ПАУЗЫ в конце. Запустите build_assets.py!")
                meme_section.append(str(meme_pause_path))
            print(f"-> Добавляем МЕМНУЮ ПАУЗУ ({len(meme_clips_list)} мемов)...")
        else:
            print("ВНИМАНИЕ: assets/meme_pause.mp4 не найдено. Мемная пауза пропущена. Запустите build_assets.py!")

    # Финальный порядок: intro -> до мемов -> [мемная пауза] -> после мемов -> outro
    ordered: list[str] = []

    intro_path = ASSETS_DIR / "intro.mp4"
    if intro_path.exists():
        print("-> Добавляем пререндер INTRO...")
        ordered.append(str(intro_path))
    else:
        print("ВНИМАНИЕ: assets/intro.mp4 не найдено. Сначала запустите build_assets.py!")

    ordered += before_memes
    ordered += meme_section
    ordered += after_memes

    outro_path = ASSETS_DIR / "outro.mp4"
    if outro_path.exists():
        print("-> Добавляем пререндер OUTRO...")
        ordered.append(str(outro_path))
    else:
        print("ВНИМАНИЕ: assets/outro.mp4 не найдено. Сначала запустите build_assets.py!")

    if not ordered:
        sys.exit("Ошибка: нет ни одного клипа для склейки.")

    ffmpeg_bin = os.environ.get("FFMPEG_BINARY", "ffmpeg")
    ffprobe_bin = shutil.which("ffprobe") or "ffprobe"
    enc = get_encoder_config(ffmpeg_bin)

    # Лоссless-склейка MP4 через промежуточный MPEG-TS.
    # Прямой `concat -c copy` по MP4 даёт non-monotonic DTS (из-за edit lists и
    # B-кадров с отрицательным стартовым DTS) → рассинхрон. TS не имеет edit lists
    # и склеивается с чистым таймлайном. Соответствующие канону сегменты при этом
    # копируются без перекодирования; кастомные ассеты нормализуются (см. prepare_ts).
    print(f"-> Склеиваем финальное видео ({len(ordered)} сегментов)...")

    TS_DIR = TEMP_DIR / "ts"
    if TS_DIR.exists():
        shutil.rmtree(TS_DIR)
    TS_DIR.mkdir(parents=True, exist_ok=True)

    ts_paths: list[str] = []
    for i, p in enumerate(ordered):
        ts_path = TS_DIR / f"seg_{i:04d}.ts"
        try:
            prepare_ts(ffmpeg_bin, ffprobe_bin, str(Path(p).resolve()), ts_path, enc)
        except subprocess.CalledProcessError as e:
            sys.exit(f"Ошибка FFMPEG при подготовке сегмента {p} к склейке: {e}")
        ts_paths.append(str(ts_path))

    # Пишем список для concat-demuxer (экранируем одинарные кавычки в путях)
    lines = []
    for p in ts_paths:
        abs_p = str(Path(p).resolve()).replace("'", "'\\''")
        lines.append(f"file '{abs_p}'")
    CONCAT_LIST_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Видео копируем без перекодирования (lossless — ради чего всё и затевалось).
    # Аудио перекодируем один раз: это выравнивает таймлайн на стыках сегментов
    # (иначе по границам AAC-кадров остаются non-monotonic DTS).
    cmd = [
        ffmpeg_bin, "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(CONCAT_LIST_PATH),
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart",
        str(OUTPUT_FILE),
    ]

    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        sys.exit(f"Ошибка FFMPEG при финальной склейке: {e}")

    shutil.rmtree(TS_DIR, ignore_errors=True)
    print(f"Готово! Финальное видео: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
