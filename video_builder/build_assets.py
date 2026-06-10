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

from moviepy import ColorClip, CompositeVideoClip, TextClip, ImageClip, AudioClip, AudioFileClip, VideoFileClip, vfx
import math
import numpy as np

from hw_encoder import get_encoder_config

# Константы (оставил только нужные для ассетов)
BASE_DIR = Path(__file__).parent
W, H = 1920, 1080
TITLE_FONT_PATH = str(BASE_DIR / "static/Roboto-Bold.ttf")
NUMBER_FONT_PATH = str(BASE_DIR / "static/BulbasaurSP.otf")
LEADERBOARD_FONT_PATH = str(BASE_DIR / "static/Onest-SemiBold.ttf")
EPISODE_NUMBER_FONT_PATH = str(BASE_DIR / "static/Ck Blockhead.ttf")
LEADERBOARD_BG_PATH = str(BASE_DIR / "static/leaderboard.png")
INTRO_MUSIC = str(BASE_DIR / "static/intro.mp3")
INTRO_BG_PATH = BASE_DIR / "export/intro_bg.mp4"
INTRO_LOGO_PATH = BASE_DIR / "static/intro_text.mov"
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

    if os.path.exists(LEADERBOARD_BG_PATH):
        end_bg = ImageClip(LEADERBOARD_BG_PATH).with_duration(END_DURATION)
    else:
        end_bg = ColorClip((W, H), color=(0, 0, 0), duration=END_DURATION)

    # Номер выпуска рядом с надписью "ANTIDOBROKEK" из leaderboard.png.
    # Заголовок на картинке занимает примерно x 317–1450, y 35–126.
    end_number = (
        TextClip(font=EPISODE_NUMBER_FONT_PATH, text=f"{episode}", color="#e7ff45",
                 font_size=150, size=(320, 200), text_align="left", method="caption")
        .with_duration(END_DURATION).with_position((1420, -20))
    )
    end_label = (
        TextClip(font=LEADERBOARD_FONT_PATH, text="ТОП АНТИДОБРОКЕКЕРОВ ВЫПУСКА",
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
            TextClip(font=LEADERBOARD_FONT_PATH, text=f"{i+1}. {name} – {amount}",
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
        TextClip(font=LEADERBOARD_FONT_PATH, text="ВСЕМ СПАСИБО ЗА ВИДЕО!",
                 color="white", font_size=44, size=(W, 70),
                 text_align="center", method="caption")
        .with_duration(END_DURATION - len(sorted_donors) * 0.3)
        .with_start(len(sorted_donors) * 0.3)
        .with_effects([vfx.FadeIn(0.3)])
        .with_position(("center", thanks_y))
    )

    end_clip = CompositeVideoClip(
        [end_bg, end_number, end_label] + donor_clips + [thanks_clip])

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
        TextClip(font=LEADERBOARD_FONT_PATH, text="ГЕРОИ МЕМНОЙ ПАУЗЫ", color="white", font_size=64,
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
            TextClip(font=LEADERBOARD_FONT_PATH, text=f"{i+1}. {name} – {amount}",
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
        TextClip(font=LEADERBOARD_FONT_PATH, text="СПАСИБО ЗА МЕМЫ!",
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
    parser.add_argument("--intro", action="store_true", help="Сгенерировать интро (intro.mp4)")
    parser.add_argument("--outro", action="store_true", help="Сгенерировать аутро/лидерборд (outro.mp4)")
    parser.add_argument("--meme-pause", action="store_true", help="Сгенерировать мемную паузу (meme_pause.mp4)")
    parser.add_argument("--meme-heroes", action="store_true", help="Сгенерировать героев мемной паузы (meme_heroes.mp4)")
    args = parser.parse_args()

    # Если ни один флаг ассета не передан — генерим все.
    selected = {
        "intro": args.intro,
        "outro": args.outro,
        "meme_pause": args.meme_pause,
        "meme_heroes": args.meme_heroes,
    }
    if not any(selected.values()):
        selected = {k: True for k in selected}

    print(f"Выпуск #{args.episode}")
    print("Генерируем: " + ", ".join(k for k, v in selected.items() if v))

    ffmpeg_bin = os.environ.get("FFMPEG_BINARY", "ffmpeg")
    enc = get_encoder_config(ffmpeg_bin)

    def _silent_audio(duration: float, fps: int = 48000):
        """Тихая стерео-дорожка нужной длины (для ассетов без звука)."""
        def make_frame(t):
            t = np.asarray(t)
            return np.zeros((t.shape[0], 2)) if t.ndim else np.zeros(2)
        return AudioClip(make_frame, duration=duration, fps=fps)

    def render(clip, filename):
        # Гарантируем аудиодорожку и единый формат аудио (aac 48к стерео),
        # чтобы ассеты можно было склеить с клипами через concat -c copy.
        if clip.audio is None:
            clip = clip.with_audio(_silent_audio(clip.duration))
        kwargs = dict(enc["moviepy_kwargs"])
        ffmpeg_params = list(kwargs.pop("ffmpeg_params", [])) + ["-ar", "48000", "-ac", "2"]
        clip.write_videofile(
            str(ASSETS_DIR / filename),
            fps=30,
            codec=enc["codec"],
            audio_codec="aac",
            audio_bitrate="192k",
            threads=multiprocessing.cpu_count(),
            ffmpeg_params=ffmpeg_params,
            **kwargs,
        )

    if selected["intro"]:
        print("Рендерим Интро...")
        render(make_title_clip(args.episode), "intro.mp4")

    if selected["outro"]:
        print("Собираем статистику для Аутро...")
        stats = get_statistics()
        print("Рендерим Аутро (End clip)...")
        render(make_end_clip(stats, args.episode), "outro.mp4")

    if selected["meme_pause"]:
        print("Рендерим Мемную Паузу...")
        render(make_meme_pause_clip(), "meme_pause.mp4")

    if selected["meme_heroes"]:
        print("Собираем статистику по фото для Героев мемной паузы...")
        photo_stats = get_photo_statistics()
        print("Рендерим Героев мемной паузы...")
        render(make_meme_heroes_clip(photo_stats), "meme_heroes.mp4")

    print("Готово! Ассеты (" + ", ".join(k for k, v in selected.items() if v) + ") сохранены в папку assets/")

if __name__ == "__main__":
    main()