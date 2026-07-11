# AGENTS.md

This file provides guidance to coding agents when working with code in this repository.

## Project Overview

Dobrokek Bot is a Telegram bot that lets whitelisted users share TikToks, Instagram Reels, and YouTube Shorts to a shared Telegram channel. There is also a companion video compilation builder in `video_builder/` (separate Poetry sub-project).

## Commands

### Bot (root directory)
```bash
poetry install          # Install dependencies
python main.py          # Run the bot locally

docker-compose up       # Run in Docker
docker-compose down     # Stop Docker
```

### Video Builder (video_builder/ directory)
```bash
cd video_builder
make install            # Install separate dependencies

make assets EP=<N>      # Generate intro/title clips for episode N
make clips              # Encode raw clips → temp_clips/ via FFmpeg NVENC
make video              # Assemble final video → result/output.mp4
make all EP=<N>         # Run assets → clips → video in sequence
make clean              # Remove temp_clips/ and result/
```

## Architecture

### Request Flow
```
User Telegram message
  ├─ [TEXT with URL] → handlers/link_message_handler.py
  │    → whitelist check → URL extraction (regex)
  │    → source detection → download → send to CHANNEL_ID
  └─ [VIDEO upload] → handlers/video_message_handler.py
       → whitelist check → forward to CHANNEL_ID
```

### Source Strategy Pattern
`sources/source.py` defines an abstract base with `supports(url) -> bool` and `download(url, path) -> str`. Three implementations:
- `sources/tiktok.py` — proxies through `kktiktok.com` via HTTP requests (same approach as reels)
- `sources/shorts.py` — uses `pytubefix`
- `sources/reels.py` — proxies through `kkinstagram.com` via HTTP requests

`link_message_handler.py` iterates the source list to find a matching one.

### Video Builder (video_builder/)
Three-step pipeline:
1. `build_assets.py --episode N` — renders animated intro/title clips into `assets/`
2. `build_clips.py` — encodes raw clips from `export/` via FFmpeg NVENC, writes `temp_clips/manifest.json`
3. `build_video.py` — reads `temp_clips/manifest.json`, assembles clips with MoviePy, outputs `result/output.mp4`

On WSL, scripts auto-detect `/usr/lib/wsl/lib` and set `LD_LIBRARY_PATH` for NVIDIA libs.

## Environment Variables
Configured in `.env` (gitignored):
- `BOT_TOKEN` — Telegram bot token
- `CHANNEL_ID` — target Telegram channel ID (negative number)
- `WHITELIST` — comma-separated Telegram user IDs allowed to use the bot
