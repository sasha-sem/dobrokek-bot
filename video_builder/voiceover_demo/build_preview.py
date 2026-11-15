#!/usr/bin/env python3
"""Build a standalone meme voiceover preview without running the main pipeline."""

import argparse
import asyncio
import json
from pathlib import Path
import random
import re
import shutil
import sys


HERE = Path(__file__).resolve().parent
VIDEO_BUILDER = HERE.parent
sys.path.insert(0, str(VIDEO_BUILDER))
from meme_speech import CHANNELS, FPS, SAMPLE_RATE, VOICES, run, validate_segments, write_narration  # noqa: E402
from voicestudio_speech import DEFAULT_VOICESTUDIO_RATE, VoiceStudio, speech_speed  # noqa: E402


def encoder_binary(requested: str | None) -> str:
    """The small preview uses CPU H.264 even when NVENC is unavailable."""
    candidate = requested or shutil.which("ffmpeg")
    if candidate and b"libx264" in run([candidate, "-hide_banner", "-encoders"]):
        return candidate
    if requested:
        raise RuntimeError("Указанный FFmpeg не содержит libx264.")
    try:
        import imageio_ffmpeg
    except ImportError as exc:
        raise RuntimeError("Нужен FFmpeg с libx264 или пакет imageio-ffmpeg.") from exc
    candidate = imageio_ffmpeg.get_ffmpeg_exe()
    if b"libx264" not in run([candidate, "-hide_banner", "-encoders"]):
        raise RuntimeError("Не найден FFmpeg с libx264.")
    return candidate


def load_samples(path: Path) -> list[dict]:
    samples = json.loads(path.read_text(encoding="utf-8"))["samples"]
    if not samples:
        raise ValueError("Список мемов пуст.")
    seen = set()
    for sample in samples:
        name = sample["id"]
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", name) or name in seen:
            raise ValueError(f"Некорректный или повторяющийся id: {name}")
        seen.add(name)
        image = VIDEO_BUILDER / sample["image"]
        if not image.is_file():
            raise FileNotFoundError(image)
        validate_segments(sample["segments"])
    return samples


def load_export_samples() -> list[dict]:
    from meme_narration import NarrationLibrary

    library = NarrationLibrary()
    samples = []
    for key, entry in sorted(library.data["memes"].items()):
        if entry.get("status") != "ready" or not entry.get("enabled", True):
            continue
        image = library.input_dir / key
        if not image.is_file():
            continue
        try:
            entry = library.entry(image)
        except ValueError:
            continue
        samples.append({"id": f"meme-{len(samples) + 1}", "image": image, "segments": entry["segments"]})
        if len(samples) == 3:
            break
    if not samples:
        raise ValueError("В текущем экспорте нет мемов с готовыми репликами. Запустите make narrations "
                         "или укажите --manifest со списком мемов.")
    return samples


async def build(args: argparse.Namespace) -> None:
    local_samples = HERE / "samples.json"
    if args.manifest is not None:
        samples = load_samples(args.manifest)
    elif args.voicestudio or not local_samples.is_file():
        samples = load_export_samples()
    else:
        samples = load_samples(local_samples)
    ffmpeg = encoder_binary(args.ffmpeg)
    voicestudio = None
    output = HERE / "output"
    rate = args.rate if args.rate is not None else (DEFAULT_VOICESTUDIO_RATE if args.voicestudio else "+0%")
    if args.voicestudio:
        speech_speed(rate)
        voicestudio = VoiceStudio()
        await asyncio.to_thread(voicestudio.require_profiles, (args.voice,))
        output /= "voicestudio"
    output /= args.voice
    output.mkdir(parents=True, exist_ok=True)
    clips = []
    timeline = []
    total_duration = 0.0
    rng = random.Random(args.seed)

    for sample in samples:
        name = sample["id"]
        voice = rng.choice(VOICES) if args.voice == "random" else args.voice
        print(f"\n[{name}] voice: {voice}", flush=True)
        audio = output / f"{name}.wav"
        duration = await write_narration(sample["segments"], voice, rate, ffmpeg, audio,
                                         voicestudio=voicestudio)

        clip = output / f"{name}.mp4"
        run([
            ffmpeg, "-v", "error", "-y", "-loop", "1", "-framerate", str(FPS),
            "-i", str(VIDEO_BUILDER / sample["image"]), "-i", str(audio),
            "-map", "0:v:0", "-map", "1:a:0",
            "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,"
            "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1",
            "-af", "loudnorm=I=-16:TP=-2:LRA=11,apad",
            "-t", f"{duration:.6f}", "-r", str(FPS),
            "-c:v", "libx264", "-preset", "fast", "-tune", "stillimage",
            "-crf", "20", "-pix_fmt", "yuv420p", "-threads", "4",
            "-c:a", "aac", "-b:a", "192k", "-ar", str(SAMPLE_RATE),
            "-ac", str(CHANNELS), "-movflags", "+faststart", str(clip),
        ])
        clips.append(clip)
        timeline.append({"id": name, "voice": voice,
                         "start": round(total_duration, 3), "duration": duration})
        total_duration += duration
        print(f"  rendered: {duration:.2f}s", flush=True)

    # Only validated sample IDs appear in this list, with paths relative to it.
    concat_list = output / "concat.txt"
    concat_list.write_text("".join(f"file '{p.name}'\n" for p in clips), encoding="utf-8")
    preview = output / "preview.mp4"
    run([
        ffmpeg, "-v", "error", "-y", "-f", "concat", "-safe", "1",
        "-i", str(concat_list), "-c", "copy", "-movflags", "+faststart", str(preview),
    ])
    (output / "timeline.json").write_text(
        json.dumps({"voice": args.voice, "seed": args.seed, "rate": rate, "clips": timeline},
                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    print(f"\nГотово: {preview}\nДлительность: {total_duration:.2f}s", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, help="JSON со списком конкретных мемов для пробы")
    parser.add_argument("--voice", default="random",
                        help="random (по умолчанию) или имя конкретного голоса")
    parser.add_argument("--voicestudio", action="store_true", help="Использовать профиль VoiceStudio из --voice")
    parser.add_argument("--seed", type=int, help="Повторить случайный выбор голосов с заданным числом")
    parser.add_argument("--rate", help="Темп, например --rate=+10%%")
    parser.add_argument("--ffmpeg", help="Путь к FFmpeg с libx264")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", args.voice):
        parser.error("Недопустимое имя голоса.")
    if args.voicestudio and args.voice == "random":
        parser.error("Для --voicestudio передайте ID профиля через --voice.")
    try:
        asyncio.run(build(args))
    except (OSError, ValueError, RuntimeError, asyncio.TimeoutError) as exc:
        parser.exit(1, f"Ошибка подготовки пробы: {exc}\n")


if __name__ == "__main__":
    main()
