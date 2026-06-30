"""
Генерация звуковой дорожки: "тик" на каждый бар (с throttle 18мс как в оригинале)
и финальный "динь" когда #1 место закончило анимацию.

Сэмплрейт 48000, стерео, чтобы совпадать с форматом остального проекта (aac 48k/2ch).
"""
import numpy as np

SR = 48000


def render_tick(rng: np.random.Generator) -> np.ndarray:
    """
    Один тик: sine wave 770-803 Hz с экспоненциальным spike затуханием частоты,
    громкость 0.0201 -> 0.0001 за 25мс (exponential), длительность всего 30мс.
    Возвращает mono float32 массив.
    """
    dur = 0.03
    n = int(SR * dur)
    t = np.arange(n) / SR

    f0 = 770 + rng.random() * 33  # 770..803
    f1 = f0 * 0.8
    # exponential freq ramp over first 20ms, then hold (clip t to ramp window for the exponent)
    ramp_dur = 0.02
    k = np.clip(t / ramp_dur, 0, 1)
    freq = f0 * (f1 / f0) ** k
    # integrate instantaneous frequency to get phase (since freq varies with t)
    phase = 2 * np.pi * np.cumsum(freq) / SR
    wave = np.sin(phase)

    # gain: 0.0201 -> 0.0001 exponential over 25ms, held after (silence will just be near-zero)
    gain_dur = 0.025
    gk = np.clip(t / gain_dur, 0, 1)
    gain = 0.0201 * (0.0001 / 0.0201) ** gk
    # after gain_dur the gain stays at its end value (matches setValueAtTime/exponentialRamp semantics
    # within the ramp window; total tone length 30ms so tail is short and inaudible)

    return (wave * gain).astype(np.float32)


def render_ding() -> np.ndarray:
    """
    Два тона 880Hz и 1320Hz с задержкой 40мс между ними.
    Каждый: gain 0.0001 -> 0.09 (20ms) -> 0.0001 (500ms), длительность тона 550мс.
    Возвращает mono float32 массив длиной 40ms + 550ms = 590ms.
    """
    total_dur = 0.04 + 0.55
    n_total = int(SR * total_dur)
    out = np.zeros(n_total, dtype=np.float32)

    for i, f in enumerate([880, 1320]):
        start = i * 0.04
        dur = 0.55
        n = int(SR * dur)
        t = np.arange(n) / SR
        wave = np.sin(2 * np.pi * f * t)

        # gain: 0.0001 -> 0.09 over 20ms, then 0.09 -> 0.0001 over remaining ~500ms (from t=20ms to t=520ms within this tone)
        attack_dur = 0.02
        decay_start = 0.02
        decay_dur = 0.5
        gain = np.empty(n, dtype=np.float32)
        attack_n = int(SR * attack_dur)
        gain[:attack_n] = 0.0001 * (0.09 / 0.0001) ** (np.arange(attack_n) / max(attack_n, 1))
        decay_n = n - attack_n
        if decay_n > 0:
            dk = np.arange(decay_n) / int(SR * decay_dur)
            dk = np.clip(dk, 0, 1)
            gain[attack_n:] = 0.09 * (0.0001 / 0.09) ** dk

        tone = (wave * gain).astype(np.float32)
        start_idx = int(SR * start)
        end_idx = start_idx + len(tone)
        if end_idx > n_total:
            tone = tone[: n_total - start_idx]
            end_idx = n_total
        out[start_idx:end_idx] += tone

    return out


def build_audio_track(total_duration_s: float, tick_times_s: list[float], ding_time_s: float | None,
                       seed: int = 42, gain_multiplier: float = 1.0) -> np.ndarray:
    """
    Собирает полную стерео аудио-дорожку нужной длины.
    tick_times_s: список времён (в секундах от начала видео) когда нужно сыграть тик,
                  с throttle >= 18ms между реально воспроизведёнными тиками (как в оригинале).
    ding_time_s: время финального "диня" или None.
    gain_multiplier: П.6 - глобальный множитель громкости (например, если ролик
                     дальше сводится с фоновой музыкой и тики/динь нужно сделать
                     громче/тише относительно неё). Применяется ПЕРЕД clip,
                     так что итоговый сигнал всё равно гарантированно в [-1, 1].
    """
    n_total = int(SR * total_duration_s)
    mono = np.zeros(n_total, dtype=np.float32)
    rng = np.random.default_rng(seed)

    last_tick_time = -1.0
    for t in sorted(tick_times_s):
        if t - last_tick_time < 0.018:
            continue
        last_tick_time = t
        tick = render_tick(rng)
        start_idx = int(SR * t)
        end_idx = min(start_idx + len(tick), n_total)
        if start_idx >= n_total:
            continue
        mono[start_idx:end_idx] += tick[: end_idx - start_idx]

    if ding_time_s is not None:
        ding = render_ding()
        start_idx = int(SR * ding_time_s)
        end_idx = min(start_idx + len(ding), n_total)
        if start_idx < n_total:
            mono[start_idx:end_idx] += ding[: end_idx - start_idx]

    mono *= gain_multiplier
    # clip to avoid potential overlap clipping beyond [-1, 1]
    mono = np.clip(mono, -1.0, 1.0)
    stereo = np.stack([mono, mono], axis=1)
    return stereo


if __name__ == "__main__":
    # quick sanity test: a few ticks + a ding, write to wav for listening
    import wave

    tick_times = [0.1 + i * 0.04 for i in range(20)]
    audio = build_audio_track(2.0, tick_times, ding_time_s=1.0)

    pcm16 = (audio * 32767).astype(np.int16)
    with wave.open("/home/claude/leaderboard_test/output/audio_test.wav", "wb") as f:
        f.setnchannels(2)
        f.setsampwidth(2)
        f.setframerate(SR)
        f.writeframes(pcm16.tobytes())
    print("Saved audio_test.wav, duration:", len(audio) / SR, "s")
