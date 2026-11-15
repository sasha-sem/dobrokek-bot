"""Readable progress messages plus a persistent JSONL processing log."""

from datetime import datetime
import json
from pathlib import Path
import time

from tqdm import tqdm


class ProcessingLog:
    def __init__(self, directory: Path, name: str) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / f"{name}-{datetime.now():%Y%m%d-%H%M%S-%f}.jsonl"
        self.started = time.monotonic()

    def __call__(self, stage: str, event: str, message: str, **details) -> None:
        now = datetime.now().astimezone()
        record = {"time": now.isoformat(timespec="milliseconds"),
                  "elapsed_seconds": round(time.monotonic() - self.started, 3),
                  "stage": stage, "event": event, "message": message, **details}
        with self.path.open("a", encoding="utf-8") as output:
            output.write(json.dumps(record, ensure_ascii=False) + "\n")
        tqdm.write(f"[{now:%H:%M:%S}] [{stage}] {message}")


def emit(log, stage: str, event: str, message: str, **details) -> None:
    if log is not None:
        log(stage, event, message, **details)


def preview(text: str, limit: int = 120) -> str:
    line = " ".join(text.split())
    return line if len(line) <= limit else line[:limit - 1] + "…"
