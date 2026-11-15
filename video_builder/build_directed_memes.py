"""Build a narrated photo-meme compilation directly from a Telegram export."""

import argparse
import asyncio
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit

import numpy as np

from build_clips import VIDEO_FONT_PATH, process_meme_image
from build_video import assemble_video
from meme_direction import NUMBER_PLAN_VERSION, PLAN_VERSION, make_direction
from meme_narration import BASE_DIR, SCRIPTS_PATH, NarrationLibrary, save_json
from meme_number_speech import latin_number_line, number_spans
from meme_prosody import apply_direction, write_wav
from meme_speech import CHANNELS, SAMPLE_RATE, SAMPLE_WIDTH, silence, speech
from meme_vision import LMStudioVision
from voicestudio_speech import DEFAULT_VOICESTUDIO_RATE, VoiceStudio, speech_speed


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def local_tool(name):
    path = Path(name).expanduser()
    if "/" in name or path.is_absolute():
        path = (BASE_DIR / path).resolve()
        if path.is_file():
            return str(path)
    elif result := shutil.which(name):
        return result
    raise FileNotFoundError(f"Не найден исполняемый файл {name}")


def load_photos(export):
    photos = []
    for msg in read_json(export / "result.json")["messages"]:
        entities = msg.get("text_entities", [])
        if not msg.get("photo") or any(item.get("type") == "hashtag" and item.get("text") == "#dobrokek"
                                       for item in entities):
            continue
        senders = [right["text"].strip() for left, right in zip(entities, entities[1:])
                   if left.get("type") == "plain" and left["text"].rstrip().endswith("👤")
                   and right.get("type") == "code"]
        author = senders[0] if senders else msg.get("from", "").strip()
        photo = (export / msg["photo"]).resolve()
        if not photo.is_relative_to(export) or not photo.is_file():
            raise ValueError(f"Фото отсутствует в экспорте: {msg['photo']}")
        if not author:
            raise ValueError(f"Отправитель не указан: {msg['photo']}")
        photos.append((photo, author))
    return photos


def start_model(config):
    os.environ["LM_STUDIO_URL"] = config["url"]
    if config.get("model"):
        os.environ["LM_STUDIO_MODEL"] = config["model"]
    try:
        return LMStudioVision()
    except RuntimeError as error:
        url = urlsplit(config["url"])
        connection_failure = "Не удалось подключиться" in str(error) or "код curl 7" in str(error)
        if not (connection_failure and config.get("cli") and url.hostname in ("localhost", "127.0.0.1")):
            raise
    cli = local_tool(config["cli"])
    subprocess.run([cli, "server", "start", "--bind", "127.0.0.1", "--port", str(url.port or 1234)],
                   check=True, timeout=90)
    if config.get("gpu") is not None:
        subprocess.run([cli, "load", config["model"], "--gpu", str(config["gpu"]), "--context-length",
                        "8192", "--yes"], check=True, timeout=180)
    return LMStudioVision()


def start_voice(config, profiles):
    if config.get("voicestudio_url"):
        os.environ["VOICESTUDIO_BASE_URL"] = config["voicestudio_url"]
    studio = VoiceStudio()
    try:
        studio.require_profiles(profiles)
    except RuntimeError as error:
        if "Не удалось" not in str(error) or not config.get("voicestudio_cli"):
            raise
        subprocess.run([local_tool(config["voicestudio_cli"]), "--timeout", "35", "voices", "list"],
                       check=True, capture_output=True, timeout=100)
        studio = VoiceStudio()
        studio.require_profiles(profiles)
    return studio


def whisper_settings(config, ffmpeg):
    settings = dict(config["whisper"])
    settings["cli"] = local_tool(settings["cli"])
    settings["pitch_ffmpeg"] = local_tool(settings.get("pitch_ffmpeg", ffmpeg))
    for name in ("model", "fallback_model"):
        if settings.get(name):
            weights = (BASE_DIR / settings[name]).resolve()
            if not weights.is_file():
                raise FileNotFoundError(weights)
            settings[name] = str(weights)
    filters = subprocess.check_output([settings["pitch_ffmpeg"], "-hide_banner", "-filters"], text=True)
    if " rubberband " not in filters:
        raise ValueError("Для интонации настройте whisper.pitch_ffmpeg с фильтром rubberband.")
    return settings


@contextmanager
def lock_work(path):
    with (path / ".lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError(f"Сборка уже работает в {path}") from error
        yield


def narration_entry(library, photo):
    try:
        entry = library.entry(photo)
    except (KeyError, ValueError):
        entry = {}
    if not entry or entry["status"] == "error":
        print(f"Распознаю {photo.name}", flush=True)
        library.prepare([photo])
        entry = library.entry(photo)
    if entry["status"] == "error":
        raise RuntimeError(f"{photo.name}: {entry['error']}")
    return entry


def fingerprint(*inputs):
    digest = hashlib.sha256(json.dumps(inputs, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    return digest.hexdigest()


async def synthesize(plan, profile, rate, ffmpeg, studio, photo):
    pcm = bytearray(silence(.45))
    bytes_per_second = SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH
    times = []
    for index, line in enumerate(plan["segments"], 1):
        start = len(pcm) / bytes_per_second
        pcm.extend(await speech(line["text"], profile, rate, ffmpeg, image=photo.name, segment=index,
                                total_segments=len(plan["segments"]), voicestudio=studio,
                                voicestudio_duration=line.get("voicestudio_duration")))
        times.append((start, len(pcm) / bytes_per_second))
        if index < len(plan["segments"]):
            pcm.extend(silence(line.get("pause_after", .45)))
    pcm.extend(silence(1.2))
    return np.frombuffer(pcm, dtype="<i2").reshape(-1, CHANNELS), times


def duration(path):
    output = subprocess.check_output(["ffprobe", "-v", "error", "-show_entries",
        "stream=codec_type,width,height:format=duration", "-of", "json", str(path)])
    info = json.loads(output)
    streams = {stream["codec_type"]: stream for stream in info["streams"]}
    video = streams.get("video", {})
    seconds = float(info["format"]["duration"])
    if (video.get("width"), video.get("height")) != (1920, 1080) or "audio" not in streams or seconds <= 0:
        raise ValueError(f"Ожидалось видео 1920×1080 со звуковой дорожкой: {path}")
    return seconds


def render_clip(folder, photo, author, entry, plan, profile, signature, model, studio, rate, settings, ffmpeg):
    audio = None
    direction = {}
    if plan:
        raw = folder / "dry.wav"
        audio = folder / "narration.wav"
        pcm, times = asyncio.run(synthesize(plan, profile, rate, ffmpeg, studio, photo))
        write_wav(raw, pcm)
        windows, applied = apply_direction(plan, times, raw, audio, model, settings, ffmpeg)
        direction = {**plan, "windows": windows, "applied": applied, "line_times": times, "voice": profile}
    output = folder / "clip.mp4"
    part = folder / "clip.partial.mp4"
    process_meme_image(str(photo), str(part), author, VIDEO_FONT_PATH, narration_path=audio)
    seconds = duration(part)
    part.replace(output)
    result = {"signature": signature, "path": str(output), "source": str(photo), "title": author,
              "duration": seconds, "role": "meme", "status": entry["status"], "direction": direction}
    save_json(folder / "clip.json", result)
    return result


def build(args):
    photos = load_photos(args.export)
    manifest = args.work_dir / "manifest.json"
    if not photos:
        if args.clips_only:
            save_json(manifest, {"clips": [], "voiced": 0, "with_emphasis": 0})
            print("В экспорте нет фото-мемов.", flush=True)
            return
        raise ValueError(f"В {args.export / 'result.json'} нет фото-мемов.")
    config = read_json(args.config)
    voices = {name.strip().casefold(): profile for name, profile in config["author_voices"].items()}
    rate = config.get("rate", DEFAULT_VOICESTUDIO_RATE)
    speech_speed(rate)
    missing = {author for _, author in photos if author.casefold() not in voices}
    if missing:
        raise ValueError("Добавьте профили в author_voices для: " + ", ".join(sorted(missing)))
    model = start_model(config["lm_studio"])
    studio = start_voice(config, tuple(sorted({voices[author.casefold()] for _, author in photos})))
    ffmpeg = os.environ["FFMPEG_BINARY"]
    settings = whisper_settings(config, ffmpeg)
    library = NarrationLibrary(args.scripts, args.export)
    clips = []
    pending = deque()
    with ThreadPoolExecutor(max_workers=2) as encoder:
        for index, (photo, author) in enumerate(photos, 1):
            prefix = f"[{index}/{len(photos)}] {author}"
            folder = args.work_dir / f"{index:04d}"
            folder.mkdir(exist_ok=True)
            entry = narration_entry(library, photo)
            profile = voices[author.casefold()]
            clip_signature = fingerprint(PLAN_VERSION, str(photo), entry, author, profile, rate,
                                         model.model, studio.model, studio.num_step, settings)
            number_lines = [segment["text"] for segment in entry.get("segments", []) if number_spans(segment["text"])]
            if number_lines:
                number_version = NUMBER_PLAN_VERSION + int(any(latin_number_line(text) for text in number_lines))
                clip_signature = fingerprint(clip_signature, number_version)
            clip_path = folder / "clip.json"
            if (folder / "clip.mp4").is_file() and clip_path.is_file():
                cached = read_json(clip_path)
                if cached["signature"] == clip_signature:
                    print(f"{prefix}: клип из кэша", flush=True)
                    clips.append(cached)
                    continue
            ready = entry.get("enabled", True) and entry["status"] == "ready"
            entry = entry if entry.get("enabled", True) else {**entry, "status":"disabled"}
            plan = None
            if ready:
                plan_path = folder / "plan.json"
                cached_plan = read_json(plan_path) if plan_path.exists() else {}
                plan = cached_plan.get("plan") if cached_plan.get("signature") == clip_signature else None
                if plan is None:
                    plan = make_direction(model, photo, entry["segments"])
                    save_json(plan_path, {"signature": clip_signature, "plan": plan})
                print(f"{prefix}: LLM выбрала {len(plan['cues'])} акцентов, {len(plan['numbers'])} числовых форм",
                      flush=True)
            else:
                print(f"{prefix}: {entry['status']}", flush=True)
            if len(pending) >= 2:
                pending.popleft().result()
            job = encoder.submit(render_clip, folder, photo, author, entry, plan, profile, clip_signature,
                                 model, studio, rate, settings, ffmpeg)
            pending.append(job)
            clips.append(job)
        clips = [item if isinstance(item, dict) else item.result() for item in clips]
    voiced = [clip for clip in clips if clip["direction"]]
    applied = sum(clip["direction"]["applied"] > 0 for clip in voiced)
    save_json(manifest, {"clips": clips, "llm": model.model, "voiced": len(voiced), "with_emphasis": applied})
    for clip in voiced:
        if not clip["direction"]["applied"]:
            print(f"Не удалось привязать интонацию к слову: {clip['source']}", flush=True)
    if args.clips_only:
        print(f"Клипы мемов готовы: {manifest}", flush=True)
        return
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".meme-video-", dir=args.output.parent) as stage:
        partial = Path(stage) / "output.mp4"
        assemble_video(manifest, partial, args.work_dir, args.work_dir / "concat.txt")
        seconds = duration(partial)
        if abs(seconds - sum(item["duration"] for item in clips)) > max(2, len(clips) * .08):
            raise ValueError("Длина готового ролика не совпадает с общей длиной клипов.")
        partial.replace(args.output)
    print(f"{args.output} · {seconds:.2f} с · озвучено {len(voiced)}/{len(clips)}, "
          f"интонация применена в {applied}/{len(voiced)}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export", type=Path, default=BASE_DIR / "export")
    parser.add_argument("--config", type=Path, default=BASE_DIR / "narrations/directed-memes.json")
    parser.add_argument("--scripts", type=Path,
                        help="Кэш распознавания; для другого экспорта по умолчанию лежит в work-dir.")
    parser.add_argument("--work-dir", type=Path, default=BASE_DIR / "temp_clips/directed-memes")
    parser.add_argument("--output", type=Path, default=BASE_DIR / "result/memes-by-author-llm.mp4")
    parser.add_argument("--clips-only", action="store_true", help="Подготовить клипы для полного выпуска.")
    args = parser.parse_args()
    for name, path in vars(args).items():
        if isinstance(path, Path):
            setattr(args, name, path.expanduser().resolve())
    if args.scripts is None:
        args.scripts = SCRIPTS_PATH if args.export == BASE_DIR / "export" else args.work_dir / "scripts.json"
    try:
        args.work_dir.mkdir(parents=True, exist_ok=True)
        with lock_work(args.work_dir):
            build(args)
    except (OSError, RuntimeError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print(f"Сборка остановилась: {error}", file=sys.stderr)
        details = getattr(error, "stderr", None)
        if details:
            if isinstance(details, bytes):
                details = details.decode(errors="replace")
            print("\n".join(details.strip().splitlines()[-12:]), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
