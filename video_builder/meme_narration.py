"""LM Studio recognition and editable, content-checked narration scripts for exported memes."""

from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

from meme_speech import validate_segments
from meme_vision import LMStudioVision, PROMPT_VERSION, server_url
from processing_log import emit
from telegram_export import find_special_meme, is_photo, media_messages


BASE_DIR = Path(__file__).resolve().parent
NARRATIONS_DIR = BASE_DIR / "narrations"
SCRIPTS_PATH = NARRATIONS_DIR / "scripts.json"


def image_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     suffix=".json", delete=False) as f:
        partial = Path(f.name)
        try:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        except Exception:
            partial.unlink(missing_ok=True)
            raise
    try:
        partial.replace(path)
    finally:
        partial.unlink(missing_ok=True)


def collect_images(input_dir: Path) -> list[Path]:
    """Use the same export filters and special first/last memes as build_clips."""
    photos = [input_dir / message["photo"]
              for message in media_messages(input_dir / "result.json") if is_photo(message)]
    for stem in ("first_meme", "last_meme"):
        if photo := find_special_meme(input_dir, stem):
            photos.append(photo)
    return list(dict.fromkeys(p.resolve() for p in photos))


class NarrationLibrary:
    def __init__(self, path: Path = SCRIPTS_PATH, input_dir: Path = BASE_DIR / "export",
                 *, log=None) -> None:
        self.path = path
        self.input_dir = input_dir.resolve()
        self.log = log
        self.summary = {}
        self.data = (json.loads(path.read_text(encoding="utf-8")) if path.exists()
                     else {"version": 1, "memes": {}})
        if self.data.get("version") != 1 or not isinstance(self.data.get("memes"), dict):
            raise ValueError(f"Неподдерживаемый формат {path}")
        self.active_keys = []

    def key(self, path: Path) -> str:
        return path.resolve().relative_to(self.input_dir).as_posix()

    def prepare(self, paths: list[Path], *, refresh: bool = False) -> None:
        started = time.monotonic()
        self.summary = {"total": len(paths), "cache_hits": 0, "recognized": 0,
                        "requests": 0, "retries": 0, "errors": 0}
        emit(self.log, "LLM", "prepare_start", f"Подготовка текстов: {len(paths)} картинок, LM Studio",
             total=len(paths), recognizer="lmstudio", scripts=str(self.path))
        vision = None
        self.active_keys = []
        for index, path in enumerate(paths, start=1):
            key = self.key(path)
            self.active_keys.append(key)
            digest = image_hash(path)
            previous = self.data["memes"].get(key)
            if previous and previous.get("image_sha256") == digest:
                valid_cache = (
                    previous.get("source") == "lmstudio"
                    and previous.get("prompt_version") == PROMPT_VERSION
                    and previous.get("server") == server_url()
                    and (not os.environ.get("LM_STUDIO_MODEL")
                         or os.environ["LM_STUDIO_MODEL"] == previous.get("model"))
                )
                if previous.get("source") == "manual" or (
                    not refresh and previous.get("status") != "error"
                    and valid_cache
                ):
                    self.summary["cache_hits"] += 1
                    emit(self.log, "LLM", "cache_hit",
                         f"[{index}/{len(paths)}] {path.name}: кэш ({previous['source']}), {previous['status']}",
                         image=path.name, index=index, total=len(paths), status=previous["status"], source=previous["source"])
                    continue
            if vision is None:
                vision = LMStudioVision(log=self.log)
            emit(self.log, "LLM", "image_start", f"[{index}/{len(paths)}] {path.name}: распознавание",
                 image=path.name, index=index, total=len(paths))
            enabled = previous.get("enabled", True) if previous and previous.get("image_sha256") == digest else True
            try:
                result = vision.extract(path)
                entry = {"image_sha256": digest, "source": "lmstudio", "enabled": enabled, **result}
                self.summary["recognized"] += 1
            except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
                self.summary["errors"] += 1
                entry = {"image_sha256": digest, "source": "lmstudio", "enabled": enabled,
                         "status": "error", "error": str(exc), "segments": [],
                         "attempts": list(vision.last_attempts)}
                emit(self.log, "LLM", "image_error", f"{path.name}: {exc}", image=path.name, error=str(exc))
            self.summary["requests"] += len(vision.last_attempts)
            self.summary["retries"] += max(0, len(vision.last_attempts) - 1)
            self.data["memes"][key] = entry
            save_json(self.path, self.data)
        self.summary["statuses"] = dict(Counter(self.data["memes"][key]["status"] for key in self.active_keys))
        self.summary["seconds"] = round(time.monotonic() - started, 3)
        emit(self.log, "LLM", "summary",
             f"Итоги: {len(paths)} картинок, кэш {self.summary['cache_hits']}, "
             f"распознано {self.summary['recognized']}, повторов {self.summary['retries']}, "
             f"ошибок {self.summary['errors']}; статусы {self.summary['statuses']}", **self.summary)

    def entry(self, path: Path) -> dict:
        entry = self.data["memes"][self.key(path)]
        if entry.get("image_sha256") != image_hash(path):
            raise ValueError(f"Изображение изменилось после распознавания: {path}")
        if entry.get("source") not in {"manual", "lmstudio"}:
            raise ValueError(f"Обновите распознавание в LM Studio: {path}")
        if entry.get("enabled", True) and entry.get("status") == "ready":
            validate_segments(entry["segments"])
        return entry

    def write_report(self) -> Path:
        report = self.path.with_suffix(".md")
        lines = ["# Тексты озвучки мемов", "",
                 "Исправления вносите в соседний scripts.json: segments, status=ready, source=manual.",
                 "enabled=false отключает озвучку. review/no_text/error остаются без голоса.", ""]
        for key in self.active_keys:
            entry = self.data["memes"][key]
            image = self.input_dir / key
            relative = os.path.relpath(image, report.parent)
            lines.extend([f"## {key}", "", f"Статус: {entry['status']}; источник: {entry['source']}", "",
                          f"![Мем](<{relative}>)", ""])
            lines.extend(f"> {s['text']}" for s in entry.get("segments", []))
            if entry.get("error"):
                lines.append(f"Ошибка: {entry['error']}")
            lines.append("")
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text("\n".join(lines), encoding="utf-8")
        return report
