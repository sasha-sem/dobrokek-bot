#!/usr/bin/env python3
import argparse
from collections import Counter
from collections.abc import Callable
import math
import multiprocessing
import os
from pathlib import Path

import numpy as np

from hw_encoder import ffmpeg_binary, get_encoder_config
from leaderboard_render import Participant, render_video
from telegram_export import is_photo, is_video, media_messages

os.environ.setdefault("FFMPEG_BINARY", ffmpeg_binary())
from moviepy import AudioClip, AudioFileClip, ColorClip, CompositeVideoClip, TextClip, VideoFileClip, vfx  # noqa: E402


BASE_DIR = Path(__file__).parent
W, H = 1920, 1080
TITLE_FONT_PATH = str(BASE_DIR / "static/Roboto-Bold.ttf")
NUMBER_FONT_PATH = str(BASE_DIR / "static/BulbasaurSP.otf")
OUTRO_MUSIC = str(BASE_DIR / "static/outro.mp3")
INTRO_BG_PATH = BASE_DIR / "export/intro_bg.mp4"
INTRO_LOGO_PATH = BASE_DIR / "static/intro_text.mov"
MEME_PAUSE_DURATION = 10.0
BPM = 150
BEAT = 60 / BPM
INPUT_FILE = BASE_DIR / "export/result.json"
MEME_PAUSE_MUSIC = str(BASE_DIR / "static/meme_pause.mp3")
BACKGROUND_MUSIC_GAIN = 0.2

HEROES_END_HOLD = 5.0
LEADERBOARD_TITLE = "ТОП АНТИДОБРОКЕКЕРОВ ВЫПУСКА"
LEADERBOARD_FOOTER = "ВСЕМ СПАСИБО ЗА ВИДЕО!"
HEROES_FOOTER = "СПАСИБО ЗА МЕМЫ!"

ASSETS_DIR = BASE_DIR / "assets"

def shake(t: float, ax: float = 6, ay: float = 4) -> tuple[float, float]:
    return math.sin(t * 47.3) * ax, math.cos(t * 31.7) * ay


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


def get_participants(messages: list[dict], matches_media: Callable[[dict], bool]) -> list[Participant]:
    counts = Counter(
        entity["text"]
        for message in messages if matches_media(message)
        for entity in message.get("text_entities", []) if entity.get("type") == "code"
    )
    return [Participant(name=name, count=count) for name, count in counts.most_common(5)]


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

    ASSETS_DIR.mkdir(exist_ok=True)
    enc = get_encoder_config(ffmpeg_binary())
    messages = []
    if selected["outro"] or selected["meme_heroes"]:
        try:
            messages = media_messages(INPUT_FILE)
        except FileNotFoundError:
            print(f"ВНИМАНИЕ: {INPUT_FILE} не найден. Статистика будет пустой.")

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
        print("Рендерим Аутро (leaderboard)...")
        render_video(
            participants=get_participants(messages, is_video),
            mode="leaderboard",
            title=LEADERBOARD_TITLE,
            footer=LEADERBOARD_FOOTER,
            episode=str(args.episode),
            output_path=str(ASSETS_DIR / "outro.mp4"),
            background_music_path=OUTRO_MUSIC,
            background_music_gain=BACKGROUND_MUSIC_GAIN,
        )

    if selected["meme_pause"]:
        print("Рендерим Мемную Паузу...")
        render(make_meme_pause_clip(), "meme_pause.mp4")

    if selected["meme_heroes"]:
        print("Собираем статистику по фото для Героев мемной паузы...")
        print("Рендерим Героев мемной паузы...")
        render_video(
            participants=get_participants(messages, is_photo),
            mode="heroes",
            show_title=False,
            footer=HEROES_FOOTER,
            episode=str(args.episode),
            output_path=str(ASSETS_DIR / "meme_heroes.mp4"),
            end_hold=HEROES_END_HOLD,
            background_music_path=MEME_PAUSE_MUSIC,
            background_music_gain=BACKGROUND_MUSIC_GAIN,
        )

    print("Готово! Ассеты (" + ", ".join(k for k, v in selected.items() if v) + ") сохранены в папку assets/")


if __name__ == "__main__":
    main()
