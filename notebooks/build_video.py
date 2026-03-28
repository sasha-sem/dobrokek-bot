#!/usr/bin/env python3
import math
import json
import os
import random
import re
from collections import defaultdict
from pathlib import Path

from moviepy import (
    AudioFileClip,
    ColorClip,
    CompositeVideoClip,
    TextClip,
    VideoFileClip,
    concatenate_videoclips,
    vfx,
)

# ── Constants ─────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent

INPUT_FILE  = BASE_DIR / "export/result.json"
INPUT_DIR   = BASE_DIR / "export"
OUTPUT_FILE = BASE_DIR / "result/output.mp4"

W, H = 1920, 1080

VIDEO_FONT_PATH = str(BASE_DIR / "static/Roboto-Regular.ttf")
TITLE_FONT_PATH = str(BASE_DIR / "static/Roboto-Bold.ttf")
TITLE_TEXT      = "АНТИДОБРОКЕК #10"
INTRO_MUSIC     = str(BASE_DIR / "static/intro.mp3")
OUTRO_MUSIC     = str(BASE_DIR / "static/outro.mp3")
TITLE_DURATION  = 30.0
TRANSITION_DURATION = 0.8

FIRST_CLIP_PATH = str(BASE_DIR / "export/first_clip.MP4")
LAST_CLIP_PATH  = str(BASE_DIR / "export/last_clip.mp4")

BPM  = 150
BEAT = 60 / BPM  # 0.4s


# ── Helpers ───────────────────────────────────────────────────────────────────
def demoji(text: str) -> str:
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


def ease_out(p: float) -> float:
    return 1 - (1 - p) ** 3


def shake(t: float, ax: float = 6, ay: float = 4) -> tuple[float, float]:
    return math.sin(t * 47.3) * ax, math.cos(t * 31.7) * ay


def fit_to_canvas(clip: VideoFileClip, w: int = W, h: int = H) -> CompositeVideoClip:
    clip_r = clip.resized(height=h)
    if clip_r.w > w:
        clip_r = clip_r.resized(width=w)
    bg = ColorClip((w, h), color=(0, 0, 0), duration=clip.duration)
    return CompositeVideoClip([bg, clip_r.with_position("center")]).with_audio(clip.audio)


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
    CENTER_Y  = H // 2 - 120
    SLIDE_DUR = 0.6

    ANTI_FINAL_Y     = CENTER_Y - 140
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

    title_bg     = ColorClip((W, H), color=(0, 0, 0), duration=TITLE_DURATION)
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

    main_dur   = TITLE_DURATION - 10.0
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

    dobrokek_flash = ColorClip((W, H), color=(128, 128, 128)).with_duration(0.06).with_start(4.0)
    reveal_flash   = ColorClip((W, H), color=(217, 217, 217)).with_duration(0.06).with_start(10.0)
    gold_flash     = ColorClip((W, H), color=(128, 100,   0)).with_duration(0.06).with_start(22.0)

    clip = CompositeVideoClip(
        [title_bg] + beat_flashes +
        [dobrokek_flash, reveal_flash, gold_flash,
         anti, dobrokek,
         main_red, main_blue, main_white,
         number_text]
    )

    if os.path.exists(INTRO_MUSIC):
        intro = AudioFileClip(INTRO_MUSIC).with_volume_scaled(1.0).subclipped(0, TITLE_DURATION)
        clip  = clip.with_audio(intro)

    return clip


# ── АД transition ─────────────────────────────────────────────────────────────
def make_ad_transition(duration: float = TRANSITION_DURATION, w: int = W, h: int = H) -> CompositeVideoClip:
    FS = 260
    BOX_W, BOX_H = 300, 320

    A_START = 0.05
    A_DUR   = duration - A_START - 0.10
    D_START = 0.25
    D_DUR   = duration - D_START - 0.10
    D_SLIDE = 0.20

    AX   = w // 2 - 130 - BOX_W // 2
    AY   = h // 2 - 70  - BOX_H // 2
    DX_F = w // 2 + 130 - BOX_W // 2
    DY_F = h // 2 + 70  - BOX_H // 2
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
    a_red   = letter("А", "#C81E1E", A_START, A_DUR, pos_a_red)
    a_blue  = letter("А", "#1E50DC", A_START, A_DUR, pos_a_blue)
    d_white = letter("Д", "white",   D_START, D_DUR, pos_d,     fade_in=0.07)
    d_red   = letter("Д", "#C81E1E", D_START, D_DUR, pos_d_red)
    d_blue  = letter("Д", "#1E50DC", D_START, D_DUR, pos_d_blue)

    return CompositeVideoClip(
        [bg, a_red, a_blue, a_white, d_red, d_blue, d_white]
    ).with_effects([vfx.FadeIn(0.10), vfx.FadeOut(0.15)])


# ── End card ──────────────────────────────────────────────────────────────────
def make_end_clip(statistics: dict) -> CompositeVideoClip:
    END_DURATION     = 4.65
    DONOR_FONT_SIZES = [72, 58, 48, 40, 34]
    DONOR_COLORS     = ["#FFD700", "#C0C0C0", "#CD7F32", "#AAAAAA", "#AAAAAA"]

    sorted_donors = sorted(statistics.items(), key=lambda x: x[1], reverse=True)[:5]

    end_bg    = ColorClip((W, H), color=(0, 0, 0), duration=END_DURATION)
    end_title = (
        TextClip(font=TITLE_FONT_PATH, text=TITLE_TEXT, color="white", font_size=64,
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
        fs    = DONOR_FONT_SIZES[i] if i < len(DONOR_FONT_SIZES) else 30
        color = DONOR_COLORS[i]     if i < len(DONOR_COLORS)     else "#AAAAAA"
        y     = DONORS_START_Y + sum(
            int((DONOR_FONT_SIZES[j] if j < len(DONOR_FONT_SIZES) else 30) * 1.6)
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

    end_clip = CompositeVideoClip([end_bg, end_title, end_label] + donor_clips + [thanks_clip])

    if os.path.exists(OUTRO_MUSIC):
        outro    = AudioFileClip(OUTRO_MUSIC).with_volume_scaled(0.2).subclipped(0, END_DURATION)
        end_clip = end_clip.with_audio(outro)

    return end_clip


# ── Main ──────────────────────────────────────────────────────────────────────
def main() -> None:
    with open(INPUT_FILE, "r") as f:
        export_dict = json.load(f)

    statistics = defaultdict(int)
    videos: list[dict] = []
    for message in random.sample(export_dict["messages"], k=len(export_dict["messages"])):
        if message.get("media_type") != "video_file":
            continue
        title, skip = "", False
        for entity in message["text_entities"]:
            if entity["type"] == "hashtag" and entity["text"] == "#dobrokek":
                skip = True
                break
            elif entity["type"] == "code":
                statistics[entity["text"]] += 1
            elif entity["type"] in ("plain", "code"):
                title += entity["text"].replace("\n\n", " ").strip() + " "
        if skip:
            continue
        videos.append({
            "filepath": str(INPUT_DIR / message["file"]),
            "title": demoji(title).strip(),
        })

    clips = [make_title_clip()]

    if os.path.exists(FIRST_CLIP_PATH):
        clips.append(fit_to_canvas(VideoFileClip(FIRST_CLIP_PATH)))
    else:
        print(f"[skip] first_clip not found: {FIRST_CLIP_PATH}")

    for video in videos:
        try:
            clip   = VideoFileClip(video["filepath"])
            clip_r = clip.resized(height=H)
            if clip_r.w > W:
                clip_r = clip_r.resized(width=W)
            caption_w = (W - clip_r.w) // 2
            bg = ColorClip((W, H), color=(0, 0, 0), duration=clip.duration)
            caption = (
                TextClip(font=VIDEO_FONT_PATH, size=(caption_w, H),
                         text=video["title"], color="white", font_size=20)
                .with_duration(clip.duration).with_position(("right", "top"))
            )
            clips.append(
                CompositeVideoClip([bg, clip_r.with_position("center"), caption])
                .with_audio(clip.audio)
            )
        except Exception as e:
            print(f"[skip] {video['filepath']}: {e}")

    if os.path.exists(LAST_CLIP_PATH):
        clips.append(fit_to_canvas(VideoFileClip(LAST_CLIP_PATH)))
    else:
        print(f"[skip] last_clip not found: {LAST_CLIP_PATH}")

    clips.append(make_end_clip(statistics))

    transition = make_ad_transition()
    final = concatenate_videoclips(clips, method="chain", transition=transition)
    final.write_videofile(
        str(OUTPUT_FILE),
        fps=30,
        threads=12,
        codec="h264_nvenc",
        ffmpeg_params=["-preset", "p4", "-cq", "23", "-b:v", "0", "-rc", "vbr"],
        audio_codec="aac",
    )


if __name__ == "__main__":
    main()
