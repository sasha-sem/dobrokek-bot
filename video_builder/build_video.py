#!/usr/bin/env python3
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from audio_normalization import normalization_filter
from hw_encoder import ffmpeg_binary, get_encoder_config

# ── Constants ─────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent

OUTPUT_FILE = BASE_DIR / "result/output.mp4"
ASSETS_DIR = BASE_DIR / "assets"
MANIFEST_PATH = BASE_DIR / "temp_clips/manifest.json"

# Канонические параметры сегмента (как у клипов из build_clips.py).
# Сегменты, которые им соответствуют, склеиваются без перекодирования (lossless).
# Несоответствующие (например, кастомные ассеты) нормализуются перекодированием.
W, H, FPS = 1920, 1080, 30
AUDIO_FLAGS = ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]


def _ffprobe(ffprobe_bin: str, path: str, stream: str, entries: str) -> list[str]:
    """Возвращает запрошенные поля первого потока stream ('v:0'/'a:0') или []."""
    res = subprocess.run(
        [ffprobe_bin, "-v", "error", "-select_streams", stream,
         "-show_entries", f"stream={entries}", "-of", "default=nk=1:nw=1", path],
        capture_output=True, text=True,
    )
    return res.stdout.split()


def prepare_ts(ffmpeg_bin: str, ffprobe_bin: str, src: str, ts_path: Path, enc: dict,
               normalize_audio: bool = True) -> None:
    """Ремуксит сегмент в MPEG-TS для чистой склейки.

    Видео канонического формата копируется без потерь. Громкость аудио
    выравнивается по сегментам; без коррекции канонический AAC копируется.
    Кастомные ассеты приводятся к канону, отсутствующий звук заменяется тишиной.
    """
    v = _ffprobe(ffprobe_bin, src, "v:0", "codec_name,width,height")
    a = _ffprobe(ffprobe_bin, src, "a:0", "codec_name,sample_rate,channels")

    video_ok = len(v) == 3 and v[0] == "h264" and v[1] == str(W) and v[2] == str(H)
    has_audio = len(a) >= 1
    audio_ok = len(a) == 3 and a[0] == "aac" and a[1] == "48000" and a[2] == "2"
    audio_filter = normalization_filter(ffmpeg_bin, src) if has_audio and normalize_audio else None

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
        if audio_ok and audio_filter is None:
            cmd += ["-c:a", "copy"]
        else:
            if audio_filter:
                cmd += ["-af", audio_filter]
            else:
                print(f"   [normalize] {Path(src).name}: аудио не канон ({'/'.join(a)}) -> перекодируем")
            cmd += AUDIO_FLAGS
    else:
        print(f"   [normalize] {Path(src).name}: нет аудио -> добавляем тишину")
        cmd += ["-map", "1:a:0", *AUDIO_FLAGS, "-shortest"]

    cmd += ["-f", "mpegts", str(ts_path)]
    subprocess.run(cmd, check=True)


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Склейка видео с выравниванием громкости клипов.")
    parser.add_argument("--no-normalize-audio", action="store_true",
                        help="Отключить выравнивание громкости (прежнее поведение).")
    args = parser.parse_args()
    try:
        OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".episode-", dir=OUTPUT_FILE.parent) as stage:
            partial = Path(stage) / OUTPUT_FILE.name
            assemble_video(MANIFEST_PATH, partial, assets_dir=ASSETS_DIR,
                           normalize_audio=not args.no_normalize_audio)
            partial.replace(OUTPUT_FILE)
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(1, f"Ошибка сборки: {error}\n")
    print(f"Готовый выпуск: {OUTPUT_FILE}")


def available_asset(assets_dir: Path | None, name: str) -> Path | None:
    if assets_dir is None:
        return None
    path = assets_dir / name
    return path if path.is_file() and path.stat().st_size > 1 else None


def ordered_clips(clips: list[dict], assets_dir: Path | None) -> list[str]:
    # Разделяем клипы по ролям (порядок внутри ролей сохраняем как в манифесте)
    before_memes: list[str] = []
    meme_clips_list: list[str] = []
    after_memes: list[str] = []
    in_memes = False

    for entry in clips:
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
    meme_pause_path = available_asset(assets_dir, "meme_pause.mp4")
    meme_heroes_path = available_asset(assets_dir, "meme_heroes.mp4")
    if meme_clips_list:
        if meme_pause_path:
            meme_section.append(str(meme_pause_path))
        elif assets_dir:
            print("ВНИМАНИЕ: assets/meme_pause.mp4 не найдено. Добавляем мемы без заставки.")
        meme_section += meme_clips_list
        if meme_heroes_path:
            meme_section.append(str(meme_heroes_path))
        elif meme_pause_path:
            print("ВНИМАНИЕ: assets/meme_heroes.mp4 не найдено. Используем заставку МЕМНОЙ ПАУЗЫ в конце. Запустите build_assets.py!")
            meme_section.append(str(meme_pause_path))
        print(f"-> Добавляем МЕМНУЮ ПАУЗУ ({len(meme_clips_list)} мемов)...")

    # Финальный порядок: intro -> до мемов -> [мемная пауза] -> после мемов -> outro
    ordered: list[str] = []

    intro_path = available_asset(assets_dir, "intro.mp4")
    if intro_path:
        print("-> Добавляем пререндер INTRO...")
        ordered.append(str(intro_path))
    elif assets_dir:
        print("ВНИМАНИЕ: assets/intro.mp4 не найдено. Сначала запустите build_assets.py!")

    ordered += before_memes
    ordered += meme_section
    ordered += after_memes

    outro_path = available_asset(assets_dir, "outro.mp4")
    if outro_path:
        print("-> Добавляем пререндер OUTRO...")
        ordered.append(str(outro_path))
    elif assets_dir:
        print("ВНИМАНИЕ: assets/outro.mp4 не найдено. Сначала запустите build_assets.py!")

    return ordered


def assemble_video(manifest_path: Path, output_file: Path, *, assets_dir: Path | None = None,
                   normalize_audio: bool = True) -> None:
    try:
        with manifest_path.open(encoding="utf-8") as f:
            clips = json.load(f)["clips"]
    except FileNotFoundError as error:
        raise ValueError(f"{manifest_path} не найден. Сначала запустите make clips.") from error
    ordered = ordered_clips(clips, assets_dir)
    if not ordered:
        raise ValueError("Нет ни одного клипа для склейки.")

    ffmpeg_bin = ffmpeg_binary()
    ffprobe_bin = shutil.which("ffprobe") or "ffprobe"
    enc = get_encoder_config(ffmpeg_bin)

    # MPEG-TS avoids timestamp gaps caused by MP4 edit lists during concatenation.
    print(f"-> Склеиваем финальное видео ({len(ordered)} сегментов)...")
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".concat-", dir=manifest_path.parent) as folder:
        stage = Path(folder)
        segments = []
        for index, src in enumerate(ordered):
            segment = stage / f"seg_{index:04d}.ts"
            try:
                prepare_ts(ffmpeg_bin, ffprobe_bin, str(Path(src).resolve()), segment, enc,
                           normalize_audio=normalize_audio)
            except (subprocess.CalledProcessError, RuntimeError) as error:
                raise RuntimeError(f"Ошибка FFmpeg при подготовке сегмента {src}: {error}") from error
            segments.append(f"file '{segment.name}'\n")

        concat_list = stage / "concat.txt"
        concat_list.write_text("".join(segments), encoding="utf-8")
        # Re-encode AAC to keep audio timestamps continuous across segment boundaries.
        cmd = [
            ffmpeg_bin, "-y", "-loglevel", "error",
            "-f", "concat", "-i", str(concat_list),
            "-c:v", "copy",
            *AUDIO_FLAGS,
            "-movflags", "+faststart",
            str(output_file),
        ]
        try:
            subprocess.run(cmd, check=True)
        except subprocess.CalledProcessError as error:
            raise RuntimeError(f"Ошибка FFmpeg при финальной склейке: {error}") from error


if __name__ == "__main__":
    main()
