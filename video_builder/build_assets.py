#!/usr/bin/env python3
import argparse
import os
import multiprocessing
from pathlib import Path
import json
from collections import defaultdict

# --- НАСТРОЙКИ ОКРУЖЕНИЯ ---
import shutil
wsl_native_path = "/usr/lib/wsl/lib"
if os.path.exists(wsl_native_path):
    os.environ["LD_LIBRARY_PATH"] = f"{wsl_native_path}:" + os.environ.get("LD_LIBRARY_PATH", "")
os.environ["FFMPEG_BINARY"] = shutil.which("ffmpeg") or "/usr/local/bin/ffmpeg"
# ------------------------------------------------

from moviepy import ColorClip, CompositeVideoClip, TextClip, AudioFileClip, VideoFileClip, vfx
import math

from hw_encoder import get_encoder_config

# Константы (оставил только нужные для ассетов)
BASE_DIR = Path(__file__).parent
W, H = 1920, 1080
TITLE_FONT_PATH = str(BASE_DIR / "static/Roboto-Bold.ttf")
NUMBER_FONT_PATH = str(BASE_DIR / "static/BulbasaurSP.otf")
INTRO_MUSIC = str(BASE_DIR / "static/intro.mp3")
INTRO_BG_PATH = BASE_DIR / "export/intro_bg.mp4"
INTRO_LOGO_PATH = BASE_DIR / "static/intro_text/ADK - Apple ProRes 4444  + Alfa no glow.mov"
MEME_PAUSE_DURATION = 10.0
TRANSITION_DURATION = 0.6
BPM = 150
BEAT = 60 / BPM
INPUT_FILE = BASE_DIR / "export/result.json"
OUTRO_MUSIC = str(BASE_DIR / "static/outro.mp3")
MEME_PAUSE_MUSIC = str(BASE_DIR / "static/meme_pause.mp3")
EPISODE: int = 1  # overridden by --episode arg in main()

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


def make_title_clip(episode: int) -> CompositeVideoClip:
    bg = VideoFileClip(str(INTRO_BG_PATH)).resized(new_size=(W, H))

    # Готовая анимированная заставка с альфа-каналом поверх фона.
    # Стартует через LOGO_START секунд после начала фонового видео.
    LOGO_START = 5.0
    logo = (
        VideoFileClip(str(INTRO_LOGO_PATH), has_mask=True)
        .resized(new_size=(W, H))
        .with_start(LOGO_START)
        .with_position(("center", "center"))
    )

    # Номер выпуска по центру кадра, шрифтом Bulbasaur — появляется после
    # анимации заставки (когда логотип отыграл), держится 5с, фейд через альфу
    BAND_H = 400
    NUMBER_DURATION = 5.0
    num_start = LOGO_START + logo.duration
    number = (
        TextClip(font=NUMBER_FONT_PATH, text=f"ВЫПУСК {episode}", color="#e7ff45",
                 font_size=170, size=(W, BAND_H), text_align="center", method="caption")
        .with_start(num_start)
        .with_duration(NUMBER_DURATION)
        .with_effects([vfx.CrossFadeIn(1.2), vfx.CrossFadeOut(1.0)])
        .with_position("center")
    )

    return CompositeVideoClip([bg, logo, number])


# ── Мемная пауза ──────────────────────────────────────────────────────────────
def make_meme_pause_clip() -> CompositeVideoClip:
    CENTER_Y = H // 2 - 100
    duration = MEME_PAUSE_DURATION

    def pos_white(t):
        sx, sy = shake(t)
        return (int(sx), int(CENTER_Y + sy))

    def pos_red(t):
        sx, sy = shake(t)
        return (int(sx - 10), int(CENTER_Y + sy - 2))

    def pos_blue(t):
        sx, sy = shake(t)
        return (int(sx + 10), int(CENTER_Y + sy + 2))

    bg = ColorClip((W, H), color=(0, 0, 0), duration=duration)
    flashes = make_beat_flashes(duration, BEAT)
    text = "МЕМНАЯ ПАУЗА"

    def make_layer(color, pos_fn):
        return (
            TextClip(font=TITLE_FONT_PATH, text=text, color=color, font_size=110,
                     size=(W, 200), text_align="center", method="caption")
            .with_duration(duration)
            .with_effects([vfx.FadeIn(0.04)])
            .with_position(pos_fn)
        )

    clip = CompositeVideoClip(
        [bg] + flashes + [make_layer("#C81E1E", pos_red), make_layer("#1E50DC", pos_blue), make_layer("white", pos_white)]
    )

    if os.path.exists(MEME_PAUSE_MUSIC):
        music = AudioFileClip(MEME_PAUSE_MUSIC).subclipped(0, duration)
        clip = clip.with_audio(music)
        print("Music added")

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

    a_white = letter("A", "white",   A_START, A_DUR, pos_a,     fade_in=0.10)
    a_red = letter("A", "#C81E1E", A_START, A_DUR, pos_a_red)
    a_blue = letter("A", "#1E50DC", A_START, A_DUR, pos_a_blue)
    d_white = letter("Д", "white",   D_START, D_DUR, pos_d,     fade_in=0.07)
    d_red = letter("Д", "#C81E1E", D_START, D_DUR, pos_d_red)
    d_blue = letter("Д", "#1E50DC", D_START, D_DUR, pos_d_blue)

    return CompositeVideoClip(
        [bg, a_red, a_blue, a_white, d_red, d_blue, d_white]
    ).with_effects([vfx.FadeIn(0.10), vfx.FadeOut(0.15)])

def get_statistics() -> dict:
    """Парсит JSON и собирает статистику для аутро."""
    if not INPUT_FILE.exists():
        print(f"ВНИМАНИЕ: {INPUT_FILE} не найден. Статистика будет пустой.")
        return {}
        
    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        export_dict = json.load(f)
        
    stats = defaultdict(int)
    for message in export_dict.get("messages", []):
        if message.get("media_type") != "video_file":
            continue
            
        skip = False
        for entity in message.get("text_entities", []):
            if entity["type"] == "hashtag" and entity["text"] == "#dobrokek":
                skip = True
                break
        if skip:
            continue
            
        for entity in message.get("text_entities", []):
            if entity["type"] == "code":
                stats[entity["text"]] += 1

    return dict(stats)

def get_photo_statistics() -> dict:
    """Парсит JSON и собирает статистику присланных фото для «Героев мемной паузы»."""
    if not INPUT_FILE.exists():
        print(f"ВНИМАНИЕ: {INPUT_FILE} не найден. Статистика фото будет пустой.")
        return {}

    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        export_dict = json.load(f)

    stats = defaultdict(int)
    for message in export_dict.get("messages", []):
        if "photo" not in message:
            continue

        skip = False
        for entity in message.get("text_entities", []):
            if entity["type"] == "hashtag" and entity["text"] == "#dobrokek":
                skip = True
                break
        if skip:
            continue

        for entity in message.get("text_entities", []):
            if entity["type"] == "code":
                stats[entity["text"]] += 1

    return dict(stats)

def make_end_clip(statistics: dict, episode: int) -> CompositeVideoClip:
    END_DURATION = 10.0
    DONOR_FONT_SIZES = [72, 58, 48, 40, 34]
    DONOR_COLORS = ["#FFD700", "#C0C0C0", "#CD7F32", "#AAAAAA", "#AAAAAA"]

    sorted_donors = sorted(
        statistics.items(), key=lambda x: x[1], reverse=True)[:5]

    end_bg = ColorClip((W, H), color=(0, 0, 0), duration=END_DURATION)
    end_title = (
        TextClip(font=TITLE_FONT_PATH, text=f"АНТИДОБРОКЕК #{episode}", color="white", font_size=64,
                 size=(W, int(H * 0.15)), text_align="center", method="caption")
        .with_duration(END_DURATION).with_position(("center", 40))
    )
    end_label = (
        TextClip(font=TITLE_FONT_PATH, text="ТОП АНТИДОБРОКЕКЕРОВ ВЫПУСКА",
                 color="#CCCCCC", font_size=40,
                 size=(W, int(H * 0.1)), text_align="center", method="caption")
        .with_duration(END_DURATION).with_position(("center", 155))
    )

    DONORS_START_Y = 290
    donor_clips = []
    for i, (name, amount) in enumerate(sorted_donors):
        fs = DONOR_FONT_SIZES[i] if i < len(DONOR_FONT_SIZES) else 30
        color = DONOR_COLORS[i] if i < len(DONOR_COLORS) else "#AAAAAA"
        y = DONORS_START_Y + sum(
            int((DONOR_FONT_SIZES[j] if j < len(
                DONOR_FONT_SIZES) else 30) * 1.6)
            for j in range(i)
        )
        donor_clips.append(
            TextClip(font=TITLE_FONT_PATH, text=f"{i+1}. {name} – {amount}",
                     color=color, font_size=fs, size=(W, int(fs * 1.6)),
                     text_align="center", method="caption")
            .with_duration(END_DURATION - i * 0.3)
            .with_start(i * 0.3)
            .with_effects([vfx.FadeIn(0.2)])
            .with_position(("center", y))
        )

    thanks_y = DONORS_START_Y + sum(
        int((DONOR_FONT_SIZES[j] if j < len(DONOR_FONT_SIZES) else 30) * 1.6)
        for j in range(len(sorted_donors))
    ) + 20
    thanks_clip = (
        TextClip(font=TITLE_FONT_PATH, text="ВСЕМ СПАСИБО ЗА ВИДЕО!",
                 color="white", font_size=44, size=(W, 70),
                 text_align="center", method="caption")
        .with_duration(END_DURATION - len(sorted_donors) * 0.3)
        .with_start(len(sorted_donors) * 0.3)
        .with_effects([vfx.FadeIn(0.3)])
        .with_position(("center", thanks_y))
    )

    end_clip = CompositeVideoClip(
        [end_bg, end_title, end_label] + donor_clips + [thanks_clip])

    if os.path.exists(OUTRO_MUSIC):
        outro = AudioFileClip(OUTRO_MUSIC).with_volume_scaled(
            0.2).subclipped(0, END_DURATION)
        end_clip = end_clip.with_audio(outro)

    return end_clip

def make_meme_heroes_clip(statistics: dict) -> CompositeVideoClip:
    """Финальная карточка мемной паузы: топ-5 авторов по числу присланных фото."""
    END_DURATION = 10.0
    HERO_FONT_SIZES = [72, 58, 48, 40, 34]
    HERO_COLORS = ["#FFD700", "#C0C0C0", "#CD7F32", "#AAAAAA", "#AAAAAA"]

    sorted_heroes = sorted(
        statistics.items(), key=lambda x: x[1], reverse=True)[:5]

    end_bg = ColorClip((W, H), color=(0, 0, 0), duration=END_DURATION)
    end_title = (
        TextClip(font=TITLE_FONT_PATH, text="ГЕРОИ МЕМНОЙ ПАУЗЫ", color="white", font_size=64,
                 size=(W, int(H * 0.15)), text_align="center", method="caption")
        .with_duration(END_DURATION).with_position(("center", 40))
    )

    HEROES_START_Y = 290
    hero_clips = []
    for i, (name, amount) in enumerate(sorted_heroes):
        fs = HERO_FONT_SIZES[i] if i < len(HERO_FONT_SIZES) else 30
        color = HERO_COLORS[i] if i < len(HERO_COLORS) else "#AAAAAA"
        y = HEROES_START_Y + sum(
            int((HERO_FONT_SIZES[j] if j < len(
                HERO_FONT_SIZES) else 30) * 1.6)
            for j in range(i)
        )
        hero_clips.append(
            TextClip(font=TITLE_FONT_PATH, text=f"{i+1}. {name} – {amount}",
                     color=color, font_size=fs, size=(W, int(fs * 1.6)),
                     text_align="center", method="caption")
            .with_duration(END_DURATION - i * 0.3)
            .with_start(i * 0.3)
            .with_effects([vfx.FadeIn(0.2)])
            .with_position(("center", y))
        )

    thanks_y = HEROES_START_Y + sum(
        int((HERO_FONT_SIZES[j] if j < len(HERO_FONT_SIZES) else 30) * 1.6)
        for j in range(len(sorted_heroes))
    ) + 20
    thanks_clip = (
        TextClip(font=TITLE_FONT_PATH, text="СПАСИБО ЗА МЕМЫ!",
                 color="white", font_size=44, size=(W, 70),
                 text_align="center", method="caption")
        .with_duration(END_DURATION - len(sorted_heroes) * 0.3)
        .with_start(len(sorted_heroes) * 0.3)
        .with_effects([vfx.FadeIn(0.3)])
        .with_position(("center", thanks_y))
    )

    end_clip = CompositeVideoClip(
        [end_bg, end_title] + hero_clips + [thanks_clip])

    music_path = MEME_PAUSE_MUSIC if os.path.exists(MEME_PAUSE_MUSIC) else OUTRO_MUSIC
    if os.path.exists(music_path):
        music = AudioFileClip(music_path).with_volume_scaled(
            0.2).subclipped(0, END_DURATION)
        end_clip = end_clip.with_audio(music)

    return end_clip

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--episode", type=int, required=True, help="Номер выпуска, например: --episode 11")
    args = parser.parse_args()

    print(f"Выпуск #{args.episode}")

    ffmpeg_bin = os.environ.get("FFMPEG_BINARY", "ffmpeg")
    enc = get_encoder_config(ffmpeg_bin)

    print("Рендерим Интро...")
    intro_clip = make_title_clip(args.episode)
    intro_clip.write_videofile(
        str(ASSETS_DIR / "intro.mp4"),
        fps=30,
        codec=enc["codec"],
        audio_codec="aac",
        threads=multiprocessing.cpu_count(),
        **enc["moviepy_kwargs"]
    )

    print("Собираем статистику для Аутро...")
    stats = get_statistics()

    print("Рендерим Аутро (End clip)...")
    outro_clip = make_end_clip(stats, args.episode)
    outro_clip.write_videofile(
        str(ASSETS_DIR / "outro.mp4"),
        fps=30,
        codec=enc["codec"],
        audio_codec="aac",
        threads=multiprocessing.cpu_count(),
        **enc["moviepy_kwargs"]
    )
    print("Рендерим Мемную Паузу...")
    meme_pause_clip = make_meme_pause_clip()
    meme_pause_clip.write_videofile(
        str(ASSETS_DIR / "meme_pause.mp4"),
        fps=30,
        codec=enc["codec"],
        audio_codec="aac",
        threads=multiprocessing.cpu_count(),
        **enc["moviepy_kwargs"]
    )

    print("Собираем статистику по фото для Героев мемной паузы...")
    photo_stats = get_photo_statistics()

    print("Рендерим Героев мемной паузы...")
    meme_heroes_clip = make_meme_heroes_clip(photo_stats)
    meme_heroes_clip.write_videofile(
        str(ASSETS_DIR / "meme_heroes.mp4"),
        fps=30,
        codec=enc["codec"],
        audio_codec="aac",
        threads=multiprocessing.cpu_count(),
        **enc["moviepy_kwargs"]
    )
    print("Готово! Ассеты (intro, outro, meme_pause, meme_heroes) сохранены в папку assets/")
    
if __name__ == "__main__":
    main()