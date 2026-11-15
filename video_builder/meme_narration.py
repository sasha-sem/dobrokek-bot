"""Local OCR and editable, content-checked narration scripts for exported memes."""

import csv
from collections import Counter
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time

from PIL import Image, ImageOps

from meme_speech import validate_segments
from meme_vision import LMStudioVision, PROMPT_VERSION, server_url
from processing_log import emit


BASE_DIR = Path(__file__).resolve().parent
NARRATIONS_DIR = BASE_DIR / "narrations"
SCRIPTS_PATH = NARRATIONS_DIR / "scripts.json"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
OCR_VERSION = 2


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
    data = json.loads((input_dir / "result.json").read_text(encoding="utf-8"))
    photos = []
    for message in data["messages"]:
        if "photo" not in message:
            continue
        if any(e.get("type") == "hashtag" and e.get("text") == "#dobrokek"
               for e in message.get("text_entities", [])):
            continue
        photos.append(input_dir / message["photo"])
    for stem in ("first_meme", "last_meme"):
        matches = sorted(p for p in input_dir.iterdir()
                         if p.is_file() and p.stem == stem and p.suffix.lower() in IMAGE_EXTENSIONS)
        if len(matches) > 1:
            raise ValueError(f"Найдено несколько файлов {stem}. Оставьте один.")
        photos.extend(matches)
    return list(dict.fromkeys(p.resolve() for p in photos))


class TesseractOCR:
    def __init__(self) -> None:
        local = BASE_DIR / ".tools" / "tesseract"
        local_binary = local / "usr" / "bin" / "tesseract"
        self.binary = os.environ.get("TESSERACT_CMD") or shutil.which("tesseract")
        self.env = os.environ.copy()
        if not self.binary and local_binary.is_file():
            self.binary = str(local_binary)
        if self.binary and Path(self.binary).resolve() == local_binary.resolve():
            lib = local / "usr" / "lib" / "x86_64-linux-gnu"
            self.env["LD_LIBRARY_PATH"] = str(lib) + os.pathsep + self.env.get("LD_LIBRARY_PATH", "")
            self.env.setdefault("TESSDATA_PREFIX", str(local / "usr/share/tesseract-ocr/5/tessdata"))
        if not self.binary:
            raise RuntimeError("Не найден Tesseract. Установите tesseract-ocr, tesseract-ocr-rus "
                               "и tesseract-ocr-eng или задайте TESSERACT_CMD.")
        result = subprocess.run([self.binary, "--list-langs"], env=self.env,
                                capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise RuntimeError(f"Не запускается Tesseract: {result.stderr.strip()}")
        languages = set(result.stdout.splitlines())
        if not {"rus", "eng"} <= languages:
            raise RuntimeError("Для Tesseract нужны языковые данные rus и eng.")

    def extract(self, path: Path) -> dict:
        with Image.open(path) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
            scale = min(3, max(1, 1400 / image.width))
            if scale > 1:
                image = image.resize((round(image.width * scale), round(image.height * scale)),
                                     Image.Resampling.LANCZOS)
            image.thumbnail((2600, 2600), Image.Resampling.LANCZOS)
            image = ImageOps.autocontrast(ImageOps.grayscale(image))
            png = io.BytesIO()
            image.save(png, format="PNG")
        result = subprocess.run([
            self.binary, "stdin", "stdout", "-l", "rus+eng", "--psm", "11",
            "-c", "tessedit_create_tsv=1",
        ], input=png.getvalue(), env=self.env, capture_output=True, timeout=60)
        if result.returncode:
            raise RuntimeError(result.stderr.decode(errors="replace").strip())
        return parse_ocr(result.stdout.decode("utf-8"))


def parse_ocr(tsv: str) -> dict:
    """Keep readable lines, their reading order, and confidence for review."""
    lines = {}
    for word in csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE):
        if word.get("level") != "5":
            continue
        text = word.get("text", "").strip()
        confidence = float(word["conf"])
        if not text or confidence < 20:
            continue
        if re.search(r"(?:https?://|www\.|t\.me/|^@)", text, re.IGNORECASE):
            continue
        key = tuple(word[k] for k in ("page_num", "block_num", "par_num", "line_num"))
        line = lines.setdefault(key, {"top": int(word["top"]), "left": int(word["left"]), "words": []})
        line["words"].append((text, confidence))
    readable = []
    for line in sorted(lines.values(), key=lambda x: (x["top"], x["left"])):
        words = line["words"]
        text = " ".join(w[0] for w in words)
        letters = sum(c.isalpha() for c in text)
        confidence = sum(w[1] * len(w[0]) for w in words) / sum(len(w[0]) for w in words)
        if letters < 3 or confidence < 55 or letters / max(1, len(text)) < 0.4:
            continue
        readable.append({"text": text, "confidence": round(confidence, 1)})
    text = "\n".join(line["text"] for line in readable)
    confidence = (sum(line["confidence"] * len(line["text"]) for line in readable)
                  / len(text.replace("\n", ""))) if readable else 0
    return {"text": text, "lines": readable, "confidence": round(confidence, 1)}


def make_segments(text: str) -> list[dict]:
    """Join wrapped lines; sentence endings provide conservative speech pauses."""
    text = re.sub(r"(?<=\w)-\n(?=\w)", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    phrases = re.split(r"(?<=[.!?…])\s+(?=[А-ЯЁA-Z])", text)
    segments = [{"text": phrase, "pause_after": 0.4} for phrase in phrases if phrase]
    if segments:
        segments[-1].pop("pause_after")
    return segments


class NarrationLibrary:
    def __init__(self, path: Path = SCRIPTS_PATH, input_dir: Path = BASE_DIR / "export",
                 *, recognizer: str = "lmstudio", log=None) -> None:
        self.path = path
        self.input_dir = input_dir.resolve()
        if recognizer not in ("lmstudio", "tesseract"):
            raise ValueError(f"Неизвестный способ распознавания: {recognizer}")
        self.recognizer = recognizer
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
        stage = "LLM" if self.recognizer == "lmstudio" else "OCR"
        emit(self.log, stage, "prepare_start", f"Подготовка текстов: {len(paths)} картинок, {self.recognizer}",
             total=len(paths), recognizer=self.recognizer, scripts=str(self.path))
        ocr = None
        self.active_keys = []
        for index, path in enumerate(paths, start=1):
            key = self.key(path)
            self.active_keys.append(key)
            digest = image_hash(path)
            previous = self.data["memes"].get(key)
            if previous and previous.get("image_sha256") == digest:
                valid_cache = (
                    previous.get("source") == "ocr" and self.recognizer == "tesseract"
                    and previous.get("ocr_version") == OCR_VERSION
                ) or (
                    previous.get("source") == "lmstudio" and self.recognizer == "lmstudio"
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
                    emit(self.log, stage, "cache_hit",
                         f"[{index}/{len(paths)}] {path.name}: кэш ({previous['source']}), {previous['status']}",
                         image=path.name, index=index, total=len(paths), status=previous["status"], source=previous["source"])
                    continue
            # Check the engine outside the per-image handler: a missing dependency
            # must fail early, before build_clips clears any existing output.
            if ocr is None:
                ocr = LMStudioVision(log=self.log) if self.recognizer == "lmstudio" else TesseractOCR()
            emit(self.log, stage, "image_start", f"[{index}/{len(paths)}] {path.name}: распознавание",
                 image=path.name, index=index, total=len(paths))
            try:
                result = ocr.extract(path)
                if self.recognizer == "lmstudio":
                    entry = {"image_sha256": digest, "source": "lmstudio", "enabled": True, **result}
                else:
                    segments = make_segments(result["text"])
                    # Glyph confidence cannot detect missing captions or UI labels.
                    entry = {"image_sha256": digest, "source": "ocr", "ocr_version": OCR_VERSION,
                             "enabled": True, "status": "review" if segments else "no_text",
                             **result, "segments": segments}
                self.summary["recognized"] += 1
            except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
                self.summary["errors"] += 1
                entry = {"image_sha256": digest, "source": self.recognizer, "enabled": True,
                         "status": "error", "error": str(exc), "segments": [],
                         "attempts": list(ocr.last_attempts) if self.recognizer == "lmstudio" else []}
                emit(self.log, stage, "image_error", f"{path.name}: {exc}", image=path.name, error=str(exc))
            if self.recognizer == "lmstudio":
                self.summary["requests"] += len(ocr.last_attempts)
                self.summary["retries"] += max(0, len(ocr.last_attempts) - 1)
            self.data["memes"][key] = entry
            save_json(self.path, self.data)
        self.summary["statuses"] = dict(Counter(self.data["memes"][key]["status"] for key in self.active_keys))
        self.summary["seconds"] = round(time.monotonic() - started, 3)
        emit(self.log, stage, "summary",
             f"Итоги: {len(paths)} картинок, кэш {self.summary['cache_hits']}, "
             f"распознано {self.summary['recognized']}, повторов {self.summary['retries']}, "
             f"ошибок {self.summary['errors']}; статусы {self.summary['statuses']}", **self.summary)

    def entry(self, path: Path) -> dict:
        entry = self.data["memes"][self.key(path)]
        if entry.get("image_sha256") != image_hash(path):
            raise ValueError(f"Изображение изменилось после распознавания: {path}")
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
