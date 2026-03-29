#!/usr/bin/env python3
import os
import multiprocessing
import json
import random
import re
from collections import defaultdict
from pathlib import Path

# --- НАСТРОЙКИ ОКРУЖЕНИЯ ---
wsl_native_path = "/usr/lib/wsl/lib"
if os.path.exists(wsl_native_path):
    os.environ["LD_LIBRARY_PATH"] = f"{wsl_native_path}:" + os.environ.get("LD_LIBRARY_PATH", "")
os.environ["FFMPEG_BINARY"] = "/usr/local/bin/ffmpeg"
# ------------------------------------------------

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

INPUT_FILE = BASE_DIR / "export/result.json"
INPUT_DIR = BASE_DIR / "export"
OUTPUT_FILE = BASE_DIR / "result/output.mp4"
ASSETS_DIR = BASE_DIR / "assets" # Папка с готовыми интро

W, H = 1920, 1080

VIDEO_FONT_PATH = str(BASE_DIR / "static/Roboto-Regular.ttf")
TITLE_FONT_PATH = str(BASE_DIR / "static/Roboto-Bold.ttf")
TITLE_TEXT = "АНТИДОБРОКЕК #10"
OUTRO_MUSIC = str(BASE_DIR / "static/outro.mp3")

FIRST_CLIP_PATH = str(BASE_DIR / "export/first_clip.MP4")
LAST_CLIP_PATH = str(BASE_DIR / "export/last_clip.mp4")

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

def fit_to_canvas(clip: VideoFileClip, w: int = W, h: int = H) -> VideoFileClip | CompositeVideoClip:
    # ОПТИМИЗАЦИЯ: Если клип уже нужного размера, просто возвращаем его, экономя CPU
    if clip.w == w and clip.h == h:
        return clip

    clip_r = clip.resized(height=h)
    if clip_r.w > w:
        clip_r = clip_r.resized(width=w)
    bg = ColorClip((w, h), color=(0, 0, 0), duration=clip.duration)
    return CompositeVideoClip([bg, clip_r.with_position("center")]).with_audio(clip.audio)

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
    
    # Сбор данных...
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

    # ОПТИМИЗАЦИЯ: Загружаем пререндер интро
    intro_path = ASSETS_DIR / "intro.mp4"
    if intro_path.exists():
        clips = [VideoFileClip(str(intro_path))]
    else:
        print("ВНИМАНИЕ: intro.mp4 не найдено. Сначала запустите build_assets.py!")
        return

    if os.path.exists(FIRST_CLIP_PATH):
        clips.append(fit_to_canvas(VideoFileClip(FIRST_CLIP_PATH)))
    else:
        print(f"[skip] first_clip not found: {FIRST_CLIP_PATH}")

    for video in videos[:3]:
        try:
            clip = VideoFileClip(video["filepath"])
            
            # ОПТИМИЗАЦИЯ: Избегаем лишних расчетов, если видео уже в 1080p
            if clip.w == W and clip.h == H:
                clips.append(clip)
                continue

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
                CompositeVideoClip(
                    [bg, clip_r.with_position("center"), caption])
                .with_audio(clip.audio)
            )
        except Exception as e:
            print(f"[skip] {video['filepath']}: {e}")

    if os.path.exists(LAST_CLIP_PATH):
        clips.append(fit_to_canvas(VideoFileClip(LAST_CLIP_PATH)))
    else:
        print(f"[skip] last_clip not found: {LAST_CLIP_PATH}")

    clips.append(make_end_clip(statistics))

    # ОПТИМИЗАЦИЯ: Загружаем пререндер перебивки
    transition_path = ASSETS_DIR / "transition.mp4"
    if transition_path.exists():
        transition = VideoFileClip(str(transition_path))
    else:
        print("ВНИМАНИЕ: transition.mp4 не найдено. Сначала запустите build_assets.py!")
        return

    final = concatenate_videoclips(
        clips, method="chain", transition=transition)
    
    # ОПТИМИЗАЦИЯ: Многопоточный рендер с параметрами для NVENC
    cores = multiprocessing.cpu_count()
    print(f"Запускаем финальный рендер в {cores} потоков...")
    
    final.write_videofile(
        str(OUTPUT_FILE),
        fps=30,
        codec="h264_nvenc",
        audio_codec="aac",
        threads=cores,
        preset="p4",
        ffmpeg_params=["-cq", "28"] # Помогает видеокарте лучше распределять битрейт
    )

if __name__ == "__main__":
    main()