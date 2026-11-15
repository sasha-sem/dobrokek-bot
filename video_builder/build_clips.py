#!/usr/bin/env python3
"""
Encodes all raw clips and memes into temp_clips/ via FFmpeg NVENC.
Writes temp_clips/manifest.json when done.
Run BEFORE build_video.py.
"""
import argparse
import asyncio
from collections import Counter
import json
import math
import os
import random
import re
import shutil
import subprocess
import tempfile
import textwrap
import time
import wave
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from tqdm import tqdm

# --- НАСТРОЙКИ ОКРУЖЕНИЯ ---
import shutil
wsl_native_path = "/usr/lib/wsl/lib"
if os.path.exists(wsl_native_path):
    os.environ["LD_LIBRARY_PATH"] = f"{wsl_native_path}:" + \
        os.environ.get("LD_LIBRARY_PATH", "")

os.environ["FFMPEG_BINARY"] = shutil.which("ffmpeg") or "/usr/local/bin/ffmpeg"
# ---------------------------

from moviepy import VideoFileClip  # noqa: E402  (must be after env setup)

from hw_encoder import get_encoder_config
from meme_narration import NarrationLibrary, SCRIPTS_PATH, save_json
from meme_speech import VOICES, write_narration_with_fallback
from processing_log import ProcessingLog, emit
from voicestudio_speech import DEFAULT_VOICESTUDIO_RATE, VoiceStudio, speech_speed

# ── Constants ─────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent

INPUT_FILE = BASE_DIR / "export/result.json"
INPUT_DIR = BASE_DIR / "export"
TEMP_DIR = BASE_DIR / "temp_clips"
MANIFEST_PATH = TEMP_DIR / "manifest.json"

W, H = 1920, 1080
VIDEO_FONT_PATH = str(BASE_DIR / "static/Roboto-Regular.ttf")

FIRST_CLIP_PATH = str(BASE_DIR / "export/first_clip.mp4")
LAST_CLIP_PATH = str(BASE_DIR / "export/last_clip.mp4")
SPECIAL_MEME_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
MEME_DURATION = 5
MEME_CAPTION_H = 140

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
    has_audio = clip.audio is not None
    clip_duration = clip.duration
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
    enc = get_encoder_config(ffmpeg_bin)

    # Нормализуем аудио: каждый клип получает aac 48к стерео. Если у исходника
    # нет звука — подмешиваем тишину (anullsrc), чтобы поток был совместим для
    # последующей склейки через concat -c copy в build_video.py.
    silence_input = ["-f", "lavfi", "-i",
                     f"anullsrc=channel_layout=stereo:sample_rate=48000:d={clip_duration:.9f}"]
    audio_flags = ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]

    if has_text:
        filter_complex = (
            f"[0:v]scale={new_w}:{new_h}[vid];"
            f"color=c=black:s={w}x{h}[bg];"
            f"[bg][vid]overlay=(W-w)/2:(H-h)/2:shortest=1[bg_vid];"
            f"[bg_vid][1:v]overlay=W-w:0:shortest=1[outv]"
        )
        # input 0 = видео, input 1 = картинка с текстом, (input 2 = тишина, если нужна)
        silence = [] if has_audio else silence_input
        audio_map = "0:a" if has_audio else "2:a"
        cmd = [
            ffmpeg_bin, "-y",
            "-i", input_path,
            "-loop", "1", "-i", temp_text_img,
            *silence,
            "-filter_complex", filter_complex,
            "-map", "[outv]", "-map", audio_map,
            *enc["ffmpeg_flags"], *audio_flags, "-r", "30", "-shortest",
            output_path,
        ]
    else:
        filter_complex = (
            f"[0:v]scale={new_w}:{new_h}[vid];"
            f"color=c=black:s={w}x{h}[bg];"
            f"[bg][vid]overlay=(W-w)/2:(H-h)/2:shortest=1[outv]"
        )
        # input 0 = видео, (input 1 = тишина, если нужна)
        silence = [] if has_audio else silence_input
        audio_map = "0:a" if has_audio else "1:a"
        cmd = [
            ffmpeg_bin, "-y",
            "-i", input_path,
            *silence,
            "-filter_complex", filter_complex,
            "-map", "[outv]", "-map", audio_map,
            *enc["ffmpeg_flags"], *audio_flags, "-r", "30", "-shortest",
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


def process_meme_image(input_path: str, output_path: str, title: str = "", font_path: str = VIDEO_FONT_PATH,
                       *, narration_path: Path | None = None) -> None:
    """Фото с подписью автора; длительность — по озвучке, без неё MEME_DURATION."""
    ffmpeg_bin = os.environ.get("FFMPEG_BINARY", "ffmpeg")
    enc = get_encoder_config(ffmpeg_bin)
    duration = MEME_DURATION
    if narration_path is not None:
        with wave.open(str(narration_path), "rb") as audio:
            duration = math.ceil(audio.getnframes() / audio.getframerate() * 30) / 30
        if duration <= 0:
            raise ValueError(f"Пустая озвучка: {narration_path}")

    photo_h = H - MEME_CAPTION_H
    has_text = bool(title.strip())
    temp_text_img = None

    if has_text:
        pil_image = draw_text_pillow(title, font_path, font_size=28, image_size=(W, MEME_CAPTION_H))
        with tempfile.NamedTemporaryFile(suffix=".tga", delete=False) as caption:
            temp_text_img = caption.name
            pil_image.save(caption, format="TGA")

    scale_pad = (
        f"scale={W}:{photo_h}:force_original_aspect_ratio=decrease,"
        f"pad={W}:{H}:(ow-iw)/2:({photo_h}-ih)/2:color=black"
    )

    # Для немых мемов сохраняем совместимую со склейкой дорожку тишины.
    audio_input = (["-i", str(narration_path)] if narration_path is not None else
                   ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"])
    audio_flags = ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]

    if has_text:
        # crop в конце страхует от нечётной высоты (1081): scale+overlay при
        # некоторых пропорциях даёт лишний пиксель, что ломает H.264 и
        # последующую lossless-склейку. Жёстко приводим к ровным W×H.
        filter_complex = (
            f"[0:v]{scale_pad}[bg];"
            f"[bg][1:v]overlay=0:{photo_h},crop={W}:{H}:0:0[outv]"
        )
        # input 0 = фото, input 1 = подпись, input 2 = озвучка или тишина
        audio_map = "2:a"
        cmd = [
            ffmpeg_bin, "-y",
            "-loop", "1", "-i", input_path,
            "-loop", "1", "-i", temp_text_img,
            *audio_input,
            "-filter_complex", filter_complex,
            "-map", "[outv]", "-map", audio_map,
            *enc["ffmpeg_flags"], *audio_flags, "-af", "apad",
            "-t", str(duration), "-r", "30",
            output_path,
        ]
    else:
        filter_complex = f"[0:v]{scale_pad},crop={W}:{H}:0:0[outv]"
        # input 0 = фото, input 1 = озвучка или тишина
        audio_map = "1:a"
        cmd = [
            ffmpeg_bin, "-y",
            "-loop", "1", "-i", input_path,
            *audio_input,
            "-filter_complex", filter_complex,
            "-map", "[outv]", "-map", audio_map,
            *enc["ffmpeg_flags"], *audio_flags, "-af", "apad",
            "-t", str(duration), "-r", "30",
            output_path,
        ]

    try:
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    finally:
        if temp_text_img is not None:
            Path(temp_text_img).unlink(missing_ok=True)


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


def load_photos() -> list[dict]:
    """Читает export/result.json, возвращает перемешанный список {filepath, title} фото-мемов."""
    with open(INPUT_FILE, encoding="utf-8") as f:
        export_dict = json.load(f)

    photos: list[dict] = []
    for message in random.sample(export_dict["messages"], k=len(export_dict["messages"])):
        if "photo" not in message:
            continue
        title, skip = "", False
        for entity in message.get("text_entities", []):
            if entity["type"] == "hashtag" and entity["text"] == "#dobrokek":
                skip = True
                break
            elif entity["type"] in ("plain", "code"):
                title += entity["text"].replace("\n\n", " ").strip() + " "
        if skip:
            continue
        photos.append({
            "filepath": str(INPUT_DIR / message["photo"]),
            "title": demoji(title).strip(),
        })
    return photos


def load_directed_clips(manifest_path: Path, photos: list[dict]) -> list[dict]:
    with manifest_path.open(encoding="utf-8") as manifest:
        clips = json.load(manifest)["clips"]
    expected = Counter(Path(photo["filepath"]).resolve() for photo in photos)
    actual = Counter(Path(clip["source"]).resolve() for clip in clips)
    if actual != expected:
        raise ValueError("Манифест озвучки не совпадает с фото-мемами текущего экспорта.")
    for clip in clips:
        if clip["role"] != "meme" or not Path(clip["path"]).is_file():
            raise ValueError(f"Не найден готовый клип мема {clip['source']}.")
    return clips


def find_special_meme(stem: str) -> Path | None:
    """Находит необязательный мем export/<stem> с поддерживаемым расширением."""
    matches = sorted(
        path
        for path in INPUT_DIR.iterdir()
        if path.is_file()
        and path.stem == stem
        and path.suffix.lower() in SPECIAL_MEME_EXTENSIONS
    )
    if len(matches) > 1:
        filenames = ", ".join(path.name for path in matches)
        raise RuntimeError(
            f"Найдено несколько файлов {stem}: {filenames}. Оставьте только один."
        )
    return matches[0] if matches else None


# ── Main ──────────────────────────────────────────────────────────────────────


def prepare_meme_audio(input_path: str, output_path: str, *, library: NarrationLibrary | None,
                       voice: str, rate: str, log=None,
                       voicestudio: VoiceStudio | None = None) -> tuple[Path | None, dict]:
    image = Path(input_path).name
    if library is None:
        return None, {"status": "disabled"}
    entry = library.entry(Path(input_path))
    status = entry["status"] if entry.get("enabled", True) else "disabled"
    narration = {"status": status}
    if status != "ready":
        explanation = {"no_text": "нет текста для чтения", "disabled": "отключено",
                       "review": "нужна проверка текста", "error": "ошибка распознавания"}.get(status, status)
        if entry.get("error"):
            narration["error"] = entry["error"]
            narration["llm_attempts"] = entry.get("attempts", [])
            explanation += f": {entry['error']}"
        emit(log, "TTS", "narration_skipped", f"{image}: без голоса — {explanation}",
             image=image, **narration)
        return None, narration
    narration.update(voice=voice, requested_voice=voice, segments=entry["segments"])
    if voicestudio is not None:
        narration.update(provider="voicestudio", model=voicestudio.model)
    target = Path(output_path).with_suffix(".wav")
    emit(log, "TTS", "narration_start",
         f"{image}: {len(entry['segments'])} реплик, выбран {voice}",
         image=image, requested_voice=voice, segments=len(entry["segments"]))
    try:
        result = asyncio.run(write_narration_with_fallback(
            entry["segments"], voice, rate, os.environ["FFMPEG_BINARY"], target, log=log, image=image,
            voicestudio=voicestudio,
        ))
        narration.update(voice=result.voice, requested_voice=result.requested_voice,
                         fallback_used=result.fallback_used, duration=result.duration,
                         stats=result.stats.to_dict())
        return target, narration
    except Exception as exc:
        narration.update(status="tts_error", error=f"{type(exc).__name__}: {exc}")
        if getattr(exc, "stats", None) is not None:
            narration["stats"] = exc.stats.to_dict()
        emit(log, "TTS", "narration_error", f"{image}: мем будет без голоса — {narration['error']}",
             image=image, **narration)
        return None, narration


def processing_summary(narrations: list[dict], llm: dict, *, encoded: int, skipped: int,
                       seconds: float, log=None) -> dict:
    counts = Counter(item["status"] for item in narrations)
    stats = [item.get("stats", {}) for item in narrations]
    tts = {"memes": len(narrations), "statuses": dict(counts),
           "fallbacks_used": sum(bool(item.get("fallback_used")) for item in narrations),
           **{name: sum(item.get(name, 0) for item in stats)
              for name in ("requests", "cache_hits", "retries", "failed_requests")}}
    problems = [{"image": item.get("image", ""), "status": item["status"], "error": item.get("error", "")}
                for item in narrations if item["status"] in ("error", "review", "tts_error")]
    summary = {"encoded": encoded, "skipped": skipped, "seconds": round(seconds, 3),
               "llm": llm, "tts": tts, "problems": problems}
    emit(log, "SUMMARY", "clips", f"Клипы: готово {encoded}, пропущено {skipped}, {seconds:.1f} с",
         encoded=encoded, skipped=skipped, seconds=round(seconds, 3))
    emit(log, "SUMMARY", "tts",
         f"Мемы: озвучено {counts['ready']} (резервным голосом {tts['fallbacks_used']}), "
         f"без текста {counts['no_text']}, отключено {counts['disabled']}, "
         f"проверить текст {counts['review']}, ошибок LLM {counts['error']}, ошибок TTS {counts['tts_error']}", **tts)
    emit(log, "SUMMARY", "tts_requests",
         f"TTS: запросов {tts['requests']}, повторов {tts['retries']}, "
         f"неудачных запросов {tts['failed_requests']}, реплик из кэша {tts['cache_hits']}")
    for problem in problems:
        emit(log, "SUMMARY", "problem",
             f"{problem['image']}: {problem['status']} — {problem['error'] or 'нужна проверка текста'}", **problem)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-meme-voiceover", action="store_true", help="Собирать мемы без озвучки")
    parser.add_argument("--meme-voice", default="random", help="random или конкретный голос")
    parser.add_argument("--voicestudio-voices", nargs="+", metavar="ID",
                        help="Озвучивать мемы сохранёнными голосами VoiceStudio")
    parser.add_argument("--meme-seed", type=int, help="Воспроизводимый выбор голосов")
    parser.add_argument("--meme-rate", help="Темп, например --meme-rate=+10%%")
    parser.add_argument("--directed-memes-manifest", type=Path, help="Готовые озвученные фото-мемы для выпуска.")
    parser.add_argument("--scripts", type=Path, default=SCRIPTS_PATH)
    parser.add_argument("--recognizer", choices=["lmstudio", "tesseract"],
                        default=os.environ.get("MEME_RECOGNIZER", "lmstudio"))
    args = parser.parse_args()
    voices = tuple(dict.fromkeys(args.voicestudio_voices)) if args.voicestudio_voices else VOICES
    rate = args.meme_rate if args.meme_rate is not None else (
        DEFAULT_VOICESTUDIO_RATE if args.voicestudio_voices else "+0%"
    )
    if args.meme_voice not in ("random", *voices):
        parser.error("--meme-voice: выберите random или один из голосов " + ", ".join(voices))
    log = ProcessingLog(args.scripts.parent / "logs", "build-clips")
    started = time.monotonic()
    log("RUN", "start", f"Сборка клипов. Подробный журнал: {log.path}", log_file=str(log.path))
    first_meme_path = find_special_meme("first_meme")
    last_meme_path = find_special_meme("last_meme")
    photos = load_photos()
    directed_clips = []
    if args.directed_memes_manifest:
        try:
            directed_clips = load_directed_clips(args.directed_memes_manifest, photos)
        except (OSError, ValueError, KeyError) as exc:
            parser.exit(1, f"Ошибка подготовки озвучки: {exc}\n")
    meme_paths = list(dict.fromkeys(
        ([first_meme_path] if first_meme_path else [])
        + [Path(p["filepath"]) for p in photos]
        + ([last_meme_path] if last_meme_path else [])
    ))
    library = None
    voicestudio = None
    selected_voices = {}
    if not (args.no_meme_voiceover or args.directed_memes_manifest) and meme_paths:
        try:
            if args.voicestudio_voices:
                speech_speed(rate)
                voicestudio = VoiceStudio()
                voicestudio.require_profiles(voices)
            library = NarrationLibrary(args.scripts, INPUT_DIR, recognizer=args.recognizer, log=log)
            library.prepare(meme_paths)
            library.write_report()
            rng = random.Random(args.meme_seed)
            voice_field = "voicestudio_voice" if voicestudio is not None else "voice"
            for path in sorted(meme_paths):
                entry = library.entry(path)
                requested = entry.get(voice_field, args.meme_voice)
                if requested not in ("random", *voices):
                    raise ValueError(f"Неизвестный голос {requested}: {path.name}")
                selected_voices[str(path)] = rng.choice(voices) if requested == "random" else requested
        except (OSError, ValueError, RuntimeError) as exc:
            log("RUN", "prepare_error", str(exc), error=str(exc))
            parser.exit(1, f"Ошибка подготовки озвучки: {exc}\n")

    TEMP_DIR.mkdir(parents=True, exist_ok=True)

    videos = load_videos()

    first_work: list[tuple[str, str, str, str]] = []
    main_work: list[tuple[str, str, str, str]] = []
    first_meme_work: list[tuple[str, str, str, str]] = []
    meme_work: list[tuple[str, str, str, str]] = []
    last_meme_work: list[tuple[str, str, str, str]] = []
    last_work: list[tuple[str, str, str, str]] = []

    if os.path.exists(FIRST_CLIP_PATH):
        first_work.append((FIRST_CLIP_PATH, str(TEMP_DIR / "processed_first.mp4"), "", "first"))
    for i, v in enumerate(videos):
        main_work.append((v["filepath"], str(TEMP_DIR / f"processed_{i}.mp4"), v["title"], "main"))
    if os.path.exists(LAST_CLIP_PATH):
        last_work.append((LAST_CLIP_PATH, str(TEMP_DIR / "processed_last.mp4"), "", "last"))

    if first_meme_path:
        first_meme_work.append((
            str(first_meme_path),
            str(TEMP_DIR / "processed_first_meme.mp4"),
            "",
            "meme",
        ))
    directed_by_path = {}
    if directed_clips:
        for j, clip in enumerate(directed_clips):
            output = str(TEMP_DIR / f"meme_{j}.mp4")
            directed_by_path[output] = clip
            meme_work.append((clip["source"], output, clip["title"], "meme"))
    else:
        for j, p in enumerate(photos):
            meme_work.append((p["filepath"], str(TEMP_DIR / f"meme_{j}.mp4"), p["title"], "meme"))
    if last_meme_path:
        last_meme_work.append((
            str(last_meme_path),
            str(TEMP_DIR / "processed_last_meme.mp4"),
            "",
            "meme",
        ))

    mid = len(main_work) // 2
    work = (
        first_work
        + main_work[:mid]
        + first_meme_work
        + meme_work
        + last_meme_work
        + main_work[mid:]
        + last_work
    )

    manifest_clips: list[dict] = []
    manifest_skipped: list[dict] = []
    narrations: list[dict] = []
    elapsed_times: list[float] = []
    total = len(work)

    bar = tqdm(work, desc="Encoding clips", unit="clip", dynamic_ncols=True)

    for input_path, output_path, title, role in bar:
        bar.set_postfix_str(f"{Path(input_path).name[:35]}", refresh=True)
        t0 = time.perf_counter()
        try:
            narration = None
            log("ENCODE", "clip_start", f"[{bar.n + 1}/{total}] {Path(input_path).name}: {role}",
                image=Path(input_path).name, index=bar.n + 1, total=total, role=role)
            directed = directed_by_path.get(output_path)
            if directed:
                shutil.copyfile(directed["path"], output_path)
                direction = directed["direction"]
                narration = {"status": directed["status"], "direction": direction}
                if direction:
                    narration.update(voice=direction["voice"], requested_voice=direction["voice"],
                                     segments=direction["segments"], provider="voicestudio")
                narrations.append({**narration, "image": Path(input_path).name})
            elif role == "meme":
                narration_path, narration = prepare_meme_audio(
                    input_path, output_path, library=library,
                    voice=selected_voices.get(str(Path(input_path)), voices[0]), rate=rate, log=log,
                    voicestudio=voicestudio,
                )
                narrations.append({**narration, "image": Path(input_path).name})
                process_meme_image(input_path, output_path, title, VIDEO_FONT_PATH,
                                   narration_path=narration_path)
            else:
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
            manifest_entry = {"role": role, "path": str(output_path), "title": title}
            if narration is not None:
                manifest_entry["narration"] = narration
                manifest_entry["source"] = input_path
            manifest_clips.append(manifest_entry)
            log("ENCODE", "clip_ready", f"{Path(input_path).name}: готово за {elapsed:.2f} с",
                image=Path(input_path).name, seconds=round(elapsed, 3), output=output_path)
        except Exception as e:
            elapsed_times.append(time.perf_counter() - t0)
            tqdm.write(f"[skip] {Path(input_path).name}: {e}")
            manifest_skipped.append({"filepath": input_path, "error": str(e)})
            log("ENCODE", "clip_error", f"{Path(input_path).name}: {type(e).__name__}: {e}",
                image=Path(input_path).name, error=str(e), stderr=getattr(e, "stderr", ""))

    summary = processing_summary(narrations, library.summary if library is not None else {},
                                 encoded=len(manifest_clips), skipped=len(manifest_skipped),
                                 seconds=time.monotonic() - started, log=log)
    save_json(MANIFEST_PATH, {"clips": manifest_clips, "skipped": manifest_skipped, "summary": summary,
                              "log_file": str(log.path)})
    tqdm.write(f"\nManifest: {MANIFEST_PATH}")
    tqdm.write(f"Готово: {len(manifest_clips)} клипов закодировано, {len(manifest_skipped)} пропущено.")
    log("RUN", "complete", f"Manifest: {MANIFEST_PATH}; подробный журнал: {log.path}",
        manifest=str(MANIFEST_PATH), log_file=str(log.path))


if __name__ == "__main__":
    main()
