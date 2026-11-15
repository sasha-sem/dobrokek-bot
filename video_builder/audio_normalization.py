"""Быстрое выравнивание громкости сегментов с ограничением резких пиков."""

import math
import re
import subprocess

TARGET_LUFS = -16.0
TRUE_PEAK_DB = -2.0  # Запас для последующего AAC-кодирования.
MAX_GAIN_DB = 18.0
NOISE_FLOOR_LUFS = -50.0
STEREO_FILTER = "aformat=sample_rates=48000:channel_layouts=stereo"


def measure_loudness(ffmpeg_bin: str, src: str) -> tuple[float, float]:
    """Измеряет только первую аудиодорожку, в формате будущего результата."""
    result = subprocess.run(
        [ffmpeg_bin, "-hide_banner", "-nostats", "-i", src,
         "-map", "0:a:0", "-vn", "-sn", "-dn",
         "-af", f"{STEREO_FILTER},ebur128=peak=true:framelog=verbose",
         "-f", "null", "-"],
        check=True, capture_output=True, text=True,
    )
    summary = result.stderr.rsplit("Summary:", 1)[-1]
    loudness = re.search(r"\bI:\s*(-?[\d.]+|[-+]?inf)\s+LUFS", summary)
    peak = re.search(r"\bPeak:\s*(-?[\d.]+|[-+]?inf)\s+dBFS", summary)
    if loudness is None or peak is None:
        raise RuntimeError(f"Не удалось измерить громкость {src}:\n{result.stderr}")
    return float(loudness[1]), float(peak[1])


def normalization_gain(loudness: float, peak: float) -> float:
    """Приближает к целевой громкости, ограничивая усиление шума."""
    if not math.isfinite(loudness) or not math.isfinite(peak):
        return 0.0
    if loudness < NOISE_FLOOR_LUFS:
        # Тишину, шум и слишком короткие для R128 фрагменты не усиливаем.
        return min(0.0, TRUE_PEAK_DB - peak)
    return min(TARGET_LUFS - loudness, MAX_GAIN_DB)


def normalization_filter(ffmpeg_bin: str, src: str) -> str | None:
    loudness, peak = measure_loudness(ffmpeg_bin, src)
    gain = normalization_gain(loudness, peak)
    needs_limiter = math.isfinite(peak) and peak + gain > TRUE_PEAK_DB
    if abs(gain) < 0.1 and not needs_limiter:
        return None
    print(f"   [loudness] {src}: {loudness:.1f} LUFS, {gain:+.1f} dB")
    # Тот же downmix/resample, что при измерении. Постоянный gain сохраняет
    # динамику; если не хватает запаса, ограничиваем только пики. Oversampling
    # учитывает межсэмпловые пики, latency компенсирует lookahead ограничителя.
    audio_filter = f"{STEREO_FILTER},volume={gain:.3f}dB"
    if needs_limiter:
        limit = 10 ** (TRUE_PEAK_DB / 20)
        audio_filter += (f",aresample=192000,alimiter=limit={limit:.8f}:"
                         "attack=5:release=50:level=false:latency=true,aresample=48000")
    return audio_filter
