"""Common filters for media in a Telegram export."""

import json
from pathlib import Path


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def media_messages(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig") as export:
        messages = json.load(export).get("messages", [])
    return [
        message for message in messages
        if not any(entity.get("type") == "hashtag" and entity.get("text") == "#dobrokek"
                   for entity in message.get("text_entities", []))
    ]


def is_video(message: dict) -> bool:
    return message.get("media_type") == "video_file"


def is_photo(message: dict) -> bool:
    return bool(message.get("photo"))


def find_special_meme(export: Path, stem: str) -> Path | None:
    matches = sorted(path for path in export.iterdir()
                     if path.stem == stem and path.suffix.lower() in IMAGE_EXTENSIONS and path.is_file())
    if len(matches) > 1:
        filenames = ", ".join(path.name for path in matches)
        raise ValueError(f"Найдено несколько файлов {stem}: {filenames}. Оставьте только один.")
    return matches[0] if matches else None
