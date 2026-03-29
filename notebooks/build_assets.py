#!/usr/bin/env python3
import os
import multiprocessing
from pathlib import Path

# --- НАСТРОЙКИ ОКРУЖЕНИЯ ---
wsl_native_path = "/usr/lib/wsl/lib"
if os.path.exists(wsl_native_path):
    os.environ["LD_LIBRARY_PATH"] = f"{wsl_native_path}:" + os.environ.get("LD_LIBRARY_PATH", "")
os.environ["FFMPEG_BINARY"] = "/usr/local/bin/ffmpeg"
# ------------------------------------------------

from moviepy import ColorClip, CompositeVideoClip, TextClip, AudioFileClip, vfx
import math

# Константы (оставил только нужные для ассетов)
BASE_DIR = Path(__file__).parent
W, H = 1920, 1080
TITLE_FONT_PATH = str(BASE_DIR / "static/Roboto-Bold.ttf")
INTRO_MUSIC = str(BASE_DIR / "static/intro.mp3")
TITLE_DURATION = 30.0
TRANSITION_DURATION = 0.8
BPM = 150
BEAT = 60 / BPM

# Создаем папку для ассетов, если её нет
ASSETS_DIR = BASE_DIR / "assets"
ASSETS_DIR.mkdir(exist_ok=True)

# --- Функции из твоеного оригинального скрипта (shake, ease_out, make_beat_flashes, make_title_clip, make_ad_transition) ---
# (Вставь сюда их без изменений, чтобы не загромождать ответ)
def ease_out(p: float) -> float: return 1 - (1 - p) ** 3
def shake(t: float, ax: float = 6, ay: float = 4) -> tuple[float, float]: return math.sin(t * 47.3) * ax, math.cos(t * 31.7) * ay
# ── Title clip ────────────────────────────────────────────────────────────────
def make_beat_flashes(duration: float, beat: float, w: int = W, h: int = H) -> list:
    flashes = []
    i, t = 0, 0.0
    while t < duration - 0.05:
        brightness = 102 if i % 4 == 0 else 31
        flashes.append(
            ColorClip((w, h), color=(brightness, brightness, brightness))
            .with_duration(0.04)
            .with_start(round(t, 4))
        )
        i += 1
        t = round(t + beat, 4)
    return flashes


def make_title_clip() -> CompositeVideoClip:
    CENTER_Y = H // 2 - 120
    SLIDE_DUR = 0.6

    ANTI_FINAL_Y = CENTER_Y - 140
    DOBROKEK_FINAL_Y = CENTER_Y + 80

    def pos_anti(t):
        progress = ease_out(min(1.0, t / SLIDE_DUR))
        y = -219 + (ANTI_FINAL_Y - (-219)) * progress
        sx, sy = shake(t) if t > SLIDE_DUR else (0, 0)
        return (int(sx), int(y + sy))

    def pos_dobrokek(t):
        progress = ease_out(min(1.0, t / SLIDE_DUR))
        y = (H - 1) + (DOBROKEK_FINAL_Y - (H - 1)) * progress
        sx, sy = shake(t) if t > SLIDE_DUR else (0, 0)
        return (int(sx), int(y + sy))

    def pos_white(t):
        sx, sy = shake(t)
        return (int(sx), int(CENTER_Y + sy))

    def pos_red(t):
        sx, sy = shake(t)
        return (int(sx - 10), int(CENTER_Y + sy - 2))

    def pos_blue(t):
        sx, sy = shake(t)
        return (int(sx + 10), int(CENTER_Y + sy + 2))

    def pos_number(t):
        sx, sy = shake(t, ax=5, ay=3)
        return (int(sx), int(CENTER_Y + 220 + sy))

    title_bg = ColorClip((W, H), color=(0, 0, 0), duration=TITLE_DURATION)
    beat_flashes = make_beat_flashes(TITLE_DURATION, BEAT)

    anti = (
        TextClip(font=TITLE_FONT_PATH, text="АНТИ", color="#FF2222", font_size=160,
                 size=(W, 220), text_align="center", method="caption")
        .with_start(BEAT).with_duration(10.0 - BEAT)
        .with_effects([vfx.FadeOut(0.05)])
        .with_position(pos_anti)
    )
    dobrokek = (
        TextClip(font=TITLE_FONT_PATH, text="ДОБРОКЕК", color="white", font_size=120,
                 size=(W, 180), text_align="center", method="caption")
        .with_start(4.0).with_duration(6.0)
        .with_effects([vfx.FadeOut(0.05)])
        .with_position(pos_dobrokek)
    )

    main_dur = TITLE_DURATION - 10.0
    main_white = (
        TextClip(font=TITLE_FONT_PATH, text="АНТИДОБРОКЕК", color="white", font_size=130,
                 size=(W, 200), text_align="center", method="caption")
        .with_start(10.0).with_duration(main_dur)
        .with_effects([vfx.FadeIn(0.04)])
        .with_position(pos_white)
    )
    main_red = (
        TextClip(font=TITLE_FONT_PATH, text="АНТИДОБРОКЕК", color="#C81E1E", font_size=130,
                 size=(W, 200), text_align="center", method="caption")
        .with_start(10.0).with_duration(main_dur)
        .with_position(pos_red)
    )
    main_blue = (
        TextClip(font=TITLE_FONT_PATH, text="АНТИДОБРОКЕК", color="#1E50DC", font_size=130,
                 size=(W, 200), text_align="center", method="caption")
        .with_start(10.0).with_duration(main_dur)
        .with_position(pos_blue)
    )
    number_text = (
        TextClip(font=TITLE_FONT_PATH, text="#10", color="#FFD700", font_size=200,
                 size=(W, 280), text_align="center", method="caption")
        .with_start(22.0).with_duration(TITLE_DURATION - 22.0)
        .with_effects([vfx.FadeIn(0.1)])
        .with_position(pos_number)
    )

    dobrokek_flash = ColorClip((W, H), color=(
        128, 128, 128)).with_duration(0.06).with_start(4.0)
    reveal_flash = ColorClip((W, H), color=(
        217, 217, 217)).with_duration(0.06).with_start(10.0)
    gold_flash = ColorClip((W, H), color=(128, 100,   0)
                           ).with_duration(0.06).with_start(22.0)

    clip = CompositeVideoClip(
        [title_bg] + beat_flashes +
        [dobrokek_flash, reveal_flash, gold_flash,
         anti, dobrokek,
         main_red, main_blue, main_white,
         number_text]
    )

    if os.path.exists(INTRO_MUSIC):
        intro = AudioFileClip(INTRO_MUSIC).with_volume_scaled(
            1.0).subclipped(0, TITLE_DURATION)
        clip = clip.with_audio(intro)

    return clip


# ── АД transition ─────────────────────────────────────────────────────────────
def make_ad_transition(duration: float = TRANSITION_DURATION, w: int = W, h: int = H) -> CompositeVideoClip:
    FS = 260
    BOX_W, BOX_H = 300, 320

    A_START = 0.05
    A_DUR = duration - A_START - 0.10
    D_START = 0.25
    D_DUR = duration - D_START - 0.10
    D_SLIDE = 0.20

    AX = w // 2 - 130 - BOX_W // 2
    AY = h // 2 - 70 - BOX_H // 2
    DX_F = w // 2 + 130 - BOX_W // 2
    DY_F = h // 2 + 70 - BOX_H // 2
    DX_S = DX_F + 110
    DY_S = DY_F + 140

    def pos_a(t):
        sx, sy = shake(t)
        return (int(AX + sx), int(AY + sy))

    def pos_a_red(t):
        sx, sy = shake(t)
        return (int(AX - 10 + sx), int(AY - 2 + sy))

    def pos_a_blue(t):
        sx, sy = shake(t)
        return (int(AX + 10 + sx), int(AY + 2 + sy))

    def pos_d(t):
        p = ease_out(min(1.0, t / D_SLIDE))
        x = DX_S + (DX_F - DX_S) * p
        y = DY_S + (DY_F - DY_S) * p
        sx, sy = shake(t) if t > D_SLIDE else (0, 0)
        return (int(x + sx), int(y + sy))

    def pos_d_red(t):
        px, py = pos_d(t)
        return (px - 10, py - 2)

    def pos_d_blue(t):
        px, py = pos_d(t)
        return (px + 10, py + 2)

    bg = ColorClip((w, h), color=(0, 0, 0), duration=duration)

    def letter(char, color, start, dur, pos_fn, fade_in=None):
        clip = (
            TextClip(font=TITLE_FONT_PATH, text=char, color=color, font_size=FS,
                     size=(BOX_W, BOX_H), text_align="center", method="caption")
            .with_start(start).with_duration(dur).with_position(pos_fn)
        )
        if fade_in:
            clip = clip.with_effects([vfx.FadeIn(fade_in)])
        return clip

    a_white = letter("А", "white",   A_START, A_DUR, pos_a,     fade_in=0.10)
    a_red = letter("А", "#C81E1E", A_START, A_DUR, pos_a_red)
    a_blue = letter("А", "#1E50DC", A_START, A_DUR, pos_a_blue)
    d_white = letter("Д", "white",   D_START, D_DUR, pos_d,     fade_in=0.07)
    d_red = letter("Д", "#C81E1E", D_START, D_DUR, pos_d_red)
    d_blue = letter("Д", "#1E50DC", D_START, D_DUR, pos_d_blue)

    return CompositeVideoClip(
        [bg, a_red, a_blue, a_white, d_red, d_blue, d_white]
    ).with_effects([vfx.FadeIn(0.10), vfx.FadeOut(0.15)])



def main():
    print("Рендерим Интро...")
    intro_clip = make_title_clip()
    intro_clip.write_videofile(
        str(ASSETS_DIR / "intro.mp4"),
        fps=30,
        codec="h264_nvenc",
        audio_codec="aac",
        threads=multiprocessing.cpu_count()
    )

    print("Рендерим Перебивку (Transition)...")
    transition_clip = make_ad_transition()
    transition_clip.write_videofile(
        str(ASSETS_DIR / "transition.mp4"),
        fps=30,
        codec="h264_nvenc",
        threads=multiprocessing.cpu_count()
    )
    print("Готово! Ассеты сохранены в папку assets/")

if __name__ == "__main__":
    main()