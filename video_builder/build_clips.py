#!/usr/bin/env python3
"""Prepare clips from a Telegram export and write the episode manifest."""
import argparse
import asyncio
from collections import Counter
import json
import random
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from tqdm import tqdm

from clip_encoding import process_meme_image, process_video_fast
from hw_encoder import ffmpeg_binary
from meme_narration import NarrationLibrary, SCRIPTS_PATH, save_json
from meme_speech import SpeechStats, write_narration
from processing_log import ProcessingLog, emit
from telegram_export import find_special_meme, is_photo, is_video, media_messages
from voicestudio_speech import DEFAULT_VOICESTUDIO_RATE, VoiceStudio, speech_speed

# ── Constants ─────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent

INPUT_FILE = BASE_DIR / "export/result.json"
INPUT_DIR = BASE_DIR / "export"
TEMP_DIR = BASE_DIR / "temp_clips"
MANIFEST_PATH = TEMP_DIR / "manifest.json"

FIRST_CLIP_PATH = str(INPUT_DIR / "first_clip.mp4")
LAST_CLIP_PATH = str(INPUT_DIR / "last_clip.mp4")


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


def titled_media(messages: list[dict], file_key: str) -> list[dict]:
    clips = []
    for message in messages:
        parts = [entity["text"].replace("\n\n", " ").strip()
                 for entity in message.get("text_entities", []) if entity.get("type") in ("plain", "code")]
        clips.append({
            "filepath": str(INPUT_DIR / message[file_key]),
            "title": demoji(" ".join(parts)).strip(),
        })
    random.shuffle(clips)
    return clips


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


def prepare_meme_audio(input_path: str, target: Path, *, library: NarrationLibrary,
                       voice: str, rate: str, voicestudio: VoiceStudio, log=None) -> tuple[Path | None, dict]:
    image = Path(input_path).name
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
    narration.update(voice=voice, requested_voice=voice, segments=entry["segments"],
                     provider="voicestudio", model=voicestudio.model)
    emit(log, "TTS", "narration_start",
         f"{image}: {len(entry['segments'])} реплик, выбран {voice}",
         image=image, requested_voice=voice, segments=len(entry["segments"]))
    stats = SpeechStats()
    try:
        duration = asyncio.run(write_narration(
            entry["segments"], voice, rate, ffmpeg_binary(), target, voicestudio=voicestudio,
            log=log, image=image, stats=stats,
        ))
        narration.update(duration=duration, stats=stats.to_dict())
        emit(log, "TTS", "narration_ready", f"{image}: озвучка готова, {voice}, {duration:.2f} с",
             image=image, voice=voice, duration=duration)
        return target, narration
    except (OSError, RuntimeError, ValueError) as exc:
        narration.update(status="tts_error", error=f"{type(exc).__name__}: {exc}", stats=stats.to_dict())
        emit(log, "TTS", "narration_error", f"{image}: мем будет без голоса — {narration['error']}",
             image=image, **narration)
        return None, narration


def processing_summary(narrations: list[dict], llm: dict, *, encoded: int, skipped: int,
                       seconds: float, log=None) -> dict:
    counts = Counter(item["status"] for item in narrations)
    stats = [item.get("stats", {}) for item in narrations]
    tts = {"memes": len(narrations), "statuses": dict(counts),
           **{name: sum(item.get(name, 0) for item in stats)
              for name in ("requests", "cache_hits", "retries", "failed_requests")}}
    problems = [{"image": item.get("image", ""), "status": item["status"], "error": item.get("error", "")}
                for item in narrations if item["status"] in ("error", "review", "tts_error")]
    summary = {"encoded": encoded, "skipped": skipped, "seconds": round(seconds, 3),
               "llm": llm, "tts": tts, "problems": problems}
    emit(log, "SUMMARY", "clips", f"Клипы: готово {encoded}, пропущено {skipped}, {seconds:.1f} с",
         encoded=encoded, skipped=skipped, seconds=round(seconds, 3))
    emit(log, "SUMMARY", "tts",
         f"Мемы: озвучено {counts['ready']}, "
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
    voice_mode = parser.add_mutually_exclusive_group()
    voice_mode.add_argument("--no-meme-voiceover", action="store_true", help="Собирать мемы без озвучки")
    voice_mode.add_argument("--voicestudio-voices", nargs="+", metavar="ID",
                            help="Выбирать голос из указанных профилей VoiceStudio")
    voice_mode.add_argument("--directed-memes-manifest", type=Path,
                            help="Использовать готовые озвученные фото-мемы.")
    parser.add_argument("--meme-voice", help="random или профиль из --voicestudio-voices")
    parser.add_argument("--meme-seed", type=int, help="Воспроизводимый выбор из --voicestudio-voices")
    parser.add_argument("--meme-rate", help="Темп для --voicestudio-voices, например --meme-rate=+10%%")
    parser.add_argument("--scripts", type=Path, default=SCRIPTS_PATH)
    args = parser.parse_args()
    if not args.voicestudio_voices and any(
        value is not None for value in (args.meme_voice, args.meme_seed, args.meme_rate)
    ):
        parser.error("--meme-voice, --meme-seed и --meme-rate требуют --voicestudio-voices.")
    voices = tuple(dict.fromkeys(args.voicestudio_voices or []))
    rate = args.meme_rate if args.meme_rate is not None else DEFAULT_VOICESTUDIO_RATE
    default_voice = args.meme_voice if args.meme_voice is not None else "random"
    if default_voice not in ("random", *voices):
        parser.error("--meme-voice: выберите random или один из профилей " + ", ".join(voices))
    log = ProcessingLog(args.scripts.parent / "logs", "build-clips")
    started = time.monotonic()
    log("RUN", "start", f"Сборка клипов. Подробный журнал: {log.path}", log_file=str(log.path))
    try:
        messages = media_messages(INPUT_FILE)
        first_meme_path = find_special_meme(INPUT_DIR, "first_meme")
        last_meme_path = find_special_meme(INPUT_DIR, "last_meme")
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Ошибка чтения экспорта: {exc}\n")
    photos = titled_media([message for message in messages if is_photo(message)], "photo")
    directed_clips = []
    library = None
    voicestudio = None
    selected_voices = {}
    try:
        if args.directed_memes_manifest:
            directed_clips = load_directed_clips(args.directed_memes_manifest, photos)
        elif voices:
            meme_paths = {Path(photo["filepath"]) for photo in photos}
            meme_paths.update(path for path in (first_meme_path, last_meme_path) if path)
            if meme_paths:
                speech_speed(rate)
                voicestudio = VoiceStudio()
                voicestudio.require_profiles(voices)
                library = NarrationLibrary(args.scripts, INPUT_DIR, log=log)
                library.prepare(sorted(meme_paths))
                library.write_report()
                rng = random.Random(args.meme_seed)
                for path in sorted(meme_paths):
                    entry = library.entry(path)
                    requested = entry.get("voicestudio_voice", default_voice)
                    if requested not in ("random", *voices):
                        raise ValueError(f"Неизвестный профиль {requested}: {path.name}")
                    selected_voices[str(path)] = rng.choice(voices) if requested == "random" else requested
        elif not args.no_meme_voiceover and photos:
            from build_directed_memes import DEFAULT_CONFIG, lock_work, prepare_clips

            work_dir = TEMP_DIR / "directed-memes"
            with lock_work(work_dir):
                directed_clips = prepare_clips(INPUT_DIR, DEFAULT_CONFIG, args.scripts, work_dir)["clips"]
    except (OSError, ValueError, RuntimeError, KeyError, subprocess.SubprocessError) as exc:
        log("RUN", "prepare_error", str(exc), error=str(exc))
        parser.exit(1, f"Ошибка подготовки озвучки: {exc}\n")

    TEMP_DIR.mkdir(parents=True, exist_ok=True)

    videos = titled_media([message for message in messages if is_video(message)], "file")
    main_work = [
        (video["filepath"], str(TEMP_DIR / f"processed_{index}.mp4"), video["title"], "main")
        for index, video in enumerate(videos)
    ]
    if directed_clips:
        photos = [{"filepath": clip["source"], "title": clip["title"]} for clip in directed_clips]
    meme_work = [
        (photo["filepath"], str(TEMP_DIR / f"meme_{index}.mp4"), photo["title"], "meme")
        for index, photo in enumerate(photos)
    ]
    directed_by_path = {work[1]: clip for work, clip in zip(meme_work, directed_clips)}
    if first_meme_path:
        meme_work.insert(0, (str(first_meme_path), str(TEMP_DIR / "processed_first_meme.mp4"), "", "meme"))
    if last_meme_path:
        meme_work.append((str(last_meme_path), str(TEMP_DIR / "processed_last_meme.mp4"), "", "meme"))

    mid = len(main_work) // 2
    work = [*main_work[:mid], *meme_work, *main_work[mid:]]
    if Path(FIRST_CLIP_PATH).exists():
        work.insert(0, (FIRST_CLIP_PATH, str(TEMP_DIR / "processed_first.mp4"), "", "first"))
    if Path(LAST_CLIP_PATH).exists():
        work.append((LAST_CLIP_PATH, str(TEMP_DIR / "processed_last.mp4"), "", "last"))

    manifest_clips: list[dict] = []
    manifest_skipped: list[dict] = []
    narrations: list[dict] = []
    total = len(work)

    bar = tqdm(work, desc="Encoding clips", unit="clip", dynamic_ncols=True)

    for index, (input_path, output_path, title, role) in enumerate(bar, 1):
        bar.set_postfix_str(Path(input_path).name[:35], refresh=True)
        t0 = time.perf_counter()
        try:
            narration = None
            log("ENCODE", "clip_start", f"[{index}/{total}] {Path(input_path).name}: {role}",
                image=Path(input_path).name, index=index, total=total, role=role)
            directed = directed_by_path.get(output_path)
            if directed:
                shutil.copyfile(directed["path"], output_path)
                direction = directed["direction"]
                narration = {"status": directed["status"], "direction": direction}
                if direction:
                    narration.update(voice=direction["voice"], requested_voice=direction["voice"],
                                     segments=direction["segments"], provider="voicestudio")
                narrations.append({**narration, "image": Path(input_path).name})
            elif role == "meme" and library is not None:
                with tempfile.TemporaryDirectory(prefix=".narration-", dir=TEMP_DIR) as folder:
                    narration_path, narration = prepare_meme_audio(
                        input_path, Path(folder) / "narration.wav", library=library,
                        voice=selected_voices[input_path], rate=rate, log=log,
                        voicestudio=voicestudio,
                    )
                    narrations.append({**narration, "image": Path(input_path).name})
                    process_meme_image(input_path, output_path, title, narration_path=narration_path)
            elif role == "meme":
                narration = {"status": "disabled"}
                narrations.append({**narration, "image": Path(input_path).name})
                process_meme_image(input_path, output_path, title)
            else:
                process_video_fast(input_path, output_path, title)
            elapsed = time.perf_counter() - t0
            manifest_entry = {"role": role, "path": str(output_path), "title": title}
            if narration is not None:
                manifest_entry["narration"] = narration
                manifest_entry["source"] = input_path
            manifest_clips.append(manifest_entry)
            log("ENCODE", "clip_ready", f"{Path(input_path).name}: готово за {elapsed:.2f} с",
                image=Path(input_path).name, seconds=round(elapsed, 3), output=output_path)
        except Exception as e:
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
