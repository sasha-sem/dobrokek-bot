"""Cached speech synthesis shared by the compilation builder and its preview."""

import asyncio
from dataclasses import asdict, dataclass, field
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import wave

from processing_log import emit, preview
from voicestudio_speech import VoiceStudio


BASE_DIR = Path(__file__).resolve().parent
SAMPLE_RATE = 48000
CHANNELS = 2
SAMPLE_WIDTH = 2
FPS = 30
VOICES = ("en-US-AndrewMultilingualNeural", "ru-RU-SvetlanaNeural")
CACHE_DIR = BASE_DIR / "narrations" / "audio"
LEGACY_CACHE_DIR = BASE_DIR / "voiceover_demo" / "cache"
SPEECH_TRIM = (
    "silenceremove=start_periods=1:start_duration=0.02:"
    "start_threshold=-55dB:start_silence=0.08"
)
SENTENCE_END = (".", "!", "?", "…")
CLOSING_PUNCTUATION = "\"'»”)]"


@dataclass
class SpeechStats:
    cache_hits: int = 0
    requests: int = 0
    retries: int = 0
    failed_requests: int = 0
    attempts: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


class SpeechSynthesisError(RuntimeError):
    def __init__(self, message: str, stats: SpeechStats) -> None:
        super().__init__(message)
        self.stats = stats


@dataclass
class NarrationResult:
    duration: float
    voice: str
    requested_voice: str
    fallback_used: bool
    stats: SpeechStats


def run(command: list[str]) -> bytes:
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors="replace")[-6000:])
    return result.stdout


def validate_segments(segments: list[dict]) -> None:
    if not isinstance(segments, list) or not segments:
        raise ValueError("Нет реплик для озвучки.")
    for segment in segments:
        if not isinstance(segment, dict):
            raise ValueError("Реплика должна содержать text и необязательную pause_after.")
        if not isinstance(segment.get("text"), str) or not segment["text"].strip():
            raise ValueError("Пустая реплика для озвучки.")
        pause = float(segment.get("pause_after", 0))
        if not math.isfinite(pause) or not 0 <= pause <= 10:
            raise ValueError("Пауза должна быть от 0 до 10 секунд.")
        duration = segment.get("voicestudio_duration")
        if duration is not None and (type(duration) not in (int, float)
                                     or not math.isfinite(duration) or duration <= 0):
            raise ValueError("Длительность реплики VoiceStudio должна быть положительным числом секунд.")


def decode_speech(path: Path, ffmpeg: str, *, trim: bool = True) -> bytes:
    filters = ["-af", f"{SPEECH_TRIM},areverse,{SPEECH_TRIM},areverse"] if trim else []
    pcm = run([
        ffmpeg, "-v", "error", "-i", str(path), "-vn", *filters,
        "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS), "-f", "s16le", "pipe:1",
    ])
    if not pcm.strip(b"\0"):
        raise RuntimeError(f"Пустая озвучка: {path}")
    return pcm


def prepare_voicestudio_segments(segments: list[dict]) -> list[dict]:
    """Keep a running sentence together and leave space between separate utterances."""
    utterances = []
    for segment in segments:
        text = segment["text"].strip()
        pause = float(segment.get("pause_after", 0))
        duration = segment.get("voicestudio_duration")
        if utterances:
            previous = utterances[-1]
            previous_text = previous["text"]
            if (duration is None and "voicestudio_duration" not in previous and not previous["pause_after"]
                    and not previous_text.rstrip(CLOSING_PUNCTUATION).endswith(SENTENCE_END)
                    and (text[0].islower() or previous_text.endswith((",", ":")))
                    and len(previous_text) + len(text) < 4096):
                previous.update(text=f"{previous_text} {text}", pause_after=pause)
                continue
        utterance = {"text": text, "pause_after": pause}
        if duration is not None:
            utterance["voicestudio_duration"] = float(duration)
        utterances.append(utterance)

    for index, utterance in enumerate(utterances):
        text = utterance["text"]
        if len(text) < 4096 and not text.rstrip(CLOSING_PUNCTUATION).endswith(SENTENCE_END):
            utterance["text"] = text.rstrip(",;:") + "."
        if index < len(utterances) - 1:
            utterance["pause_after"] = max(0.32, utterance["pause_after"])
    return utterances


async def speech(text: str, voice: str, rate: str, ffmpeg: str,
                 *, cache_dir: Path = CACHE_DIR, log=None,
                 image: str = "", segment: int = 1, total_segments: int = 1,
                 stats: SpeechStats | None = None, voicestudio: VoiceStudio | None = None,
                 voicestudio_duration: float | None = None) -> bytes:
    stats = stats if stats is not None else SpeechStats()
    context = {"image": image, "voice": voice, "segment": segment,
               "total_segments": total_segments, "characters": len(text)}
    label = f"{image}: реплика {segment}/{total_segments}, {voice}"
    provider = ["edge-tts-7.3.1"]
    audio_format = "mp3"
    if voicestudio is not None:
        provider = ["voicestudio", voicestudio.url, voicestudio.model, voicestudio.num_step]
        audio_format = "wav"
        context.update(provider="voicestudio", model=voicestudio.model)
        if voicestudio_duration is not None:
            provider.append(voicestudio_duration)
            context["target_duration"] = voicestudio_duration
    identity = json.dumps([*provider, voice, rate, text], ensure_ascii=False)
    key = hashlib.sha256(identity.encode()).hexdigest()
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / f"{key}.{audio_format}"
    cached_paths = [target]
    if voicestudio is None and LEGACY_CACHE_DIR != cache_dir:
        cached_paths.append(LEGACY_CACHE_DIR / target.name)
    for candidate in cached_paths:
        if not candidate.is_file():
            continue
        try:
            pcm = decode_speech(candidate, ffmpeg, trim=voicestudio is None)
        except RuntimeError as exc:
            emit(log, "TTS", "cache_invalid", f"{label}: повреждённый кэш, запросим заново: {exc}",
                 **context, error=str(exc))
            continue
        if candidate != target:
            shutil.copyfile(candidate, target)
        stats.cache_hits += 1
        emit(log, "TTS", "cache_hit", f"{label}: кэш, {len(pcm) / (SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH):.2f} с",
             **context, cache_path=str(target))
        return pcm

    retry_errors = (TimeoutError, RuntimeError)
    if voicestudio is None:
        try:
            from aiohttp import ClientError
            import edge_tts
        except ImportError as exc:
            raise RuntimeError("Для озвучки выполните poetry install в video_builder/.") from exc
        retry_errors += (ClientError, edge_tts.exceptions.NoAudioReceived)

    with tempfile.NamedTemporaryFile(dir=cache_dir, suffix=f".partial.{audio_format}", delete=False) as f:
        partial = Path(f.name)
    try:
        for attempt in range(3):
            started = time.monotonic()
            stats.requests += 1
            stats.retries += int(attempt > 0)
            emit(log, "TTS", "request_start",
                 f"{label}: попытка {attempt + 1}/3, {len(text)} симв. — {preview(text)}",
                 **context, attempt=attempt + 1, text=text)
            try:
                if voicestudio is not None:
                    await voicestudio.save(text, voice, rate, partial, duration=voicestudio_duration)
                else:
                    await asyncio.wait_for(
                        edge_tts.Communicate(text, voice, rate=rate).save(str(partial)), timeout=60,
                    )
                pcm = decode_speech(partial, ffmpeg, trim=voicestudio is None)
                partial.replace(target)
                elapsed = round(time.monotonic() - started, 3)
                stats.attempts.append({**context, "attempt": attempt + 1, "status": "ready", "seconds": elapsed})
                emit(log, "TTS", "request_success", f"{label}: получен звук за {elapsed:.2f} с",
                     **context, attempt=attempt + 1, seconds=elapsed)
                return pcm
            except retry_errors as exc:
                elapsed = round(time.monotonic() - started, 3)
                error = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
                stats.failed_requests += 1
                stats.attempts.append({**context, "attempt": attempt + 1, "status": "error",
                                       "seconds": elapsed, "error": error})
                emit(log, "TTS", "request_error",
                     f"{label}: попытка {attempt + 1}/3 не удалась за {elapsed:.2f} с — {error}",
                     **context, attempt=attempt + 1, seconds=elapsed, error=error)
                if attempt == 2:
                    raise SpeechSynthesisError(
                        f"{voice}, реплика {segment}/{total_segments}: после 3 попыток — {error}", stats,
                    ) from exc
                await asyncio.sleep(attempt + 1)
    finally:
        partial.unlink(missing_ok=True)


def silence(seconds: float) -> bytes:
    return b"\0" * (round(seconds * SAMPLE_RATE) * CHANNELS * SAMPLE_WIDTH)


async def write_narration(segments: list[dict], voice: str, rate: str, ffmpeg: str,
                          output: Path, *, cache_dir: Path = CACHE_DIR, log=None,
                          image: str = "", stats: SpeechStats | None = None,
                          voicestudio: VoiceStudio | None = None) -> float:
    """Write padded stereo WAV atomically and return its length rounded to a frame."""
    validate_segments(segments)
    if voicestudio is not None:
        segments = prepare_voicestudio_segments(segments)
    stats = stats if stats is not None else SpeechStats()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=output.parent, suffix=".wav", delete=False) as f:
        partial = Path(f.name)
    try:
        with wave.open(str(partial), "wb") as wav:
            wav.setparams((CHANNELS, SAMPLE_WIDTH, SAMPLE_RATE, 0, "NONE", "not compressed"))
            wav.writeframes(silence(0.45))
            for number, segment in enumerate(segments, start=1):
                wav.writeframes(await speech(segment["text"], voice, rate, ffmpeg, cache_dir=cache_dir,
                                              log=log, image=image, segment=number,
                                              total_segments=len(segments), stats=stats, voicestudio=voicestudio,
                                              voicestudio_duration=segment.get("voicestudio_duration")))
                wav.writeframes(silence(float(segment.get("pause_after", 0))))
            wav.writeframes(silence(1.2))
            frames = wav.getnframes()
        partial.replace(output)
        return math.ceil(frames / SAMPLE_RATE * FPS) / FPS
    finally:
        partial.unlink(missing_ok=True)


async def write_narration_with_fallback(segments: list[dict], voice: str, rate: str, ffmpeg: str,
                                       output: Path, *, cache_dir: Path = CACHE_DIR, log=None,
                                       image: str = "", voicestudio: VoiceStudio | None = None) -> NarrationResult:
    """Retry the entire meme with the second Edge voice when applicable."""
    stats = SpeechStats()
    candidates = [voice]
    if voicestudio is None:
        candidates.extend(candidate for candidate in VOICES if candidate != voice)
    failures = []
    for index, candidate in enumerate(candidates):
        if index:
            emit(log, "TTS", "voice_fallback",
                 f"{image}: пробуем весь мем другим голосом — {candidate}",
                 image=image, requested_voice=voice, voice=candidate)
        try:
            duration = await write_narration(segments, candidate, rate, ffmpeg, output,
                                             cache_dir=cache_dir, log=log, image=image, stats=stats,
                                             voicestudio=voicestudio)
            emit(log, "TTS", "narration_ready",
                 f"{image}: озвучка готова, {candidate}, {duration:.2f} с"
                 + (" (резервный голос)" if index else ""),
                 image=image, voice=candidate, requested_voice=voice, duration=duration,
                 fallback_used=bool(index), stats=stats.to_dict())
            return NarrationResult(duration, candidate, voice, bool(index), stats)
        except SpeechSynthesisError as exc:
            if voicestudio is not None:
                raise
            failures.append(str(exc))
    raise SpeechSynthesisError("Оба голоса не смогли озвучить мем: " + " | ".join(failures), stats)
