#!/usr/bin/env python3
"""Recognize meme text locally and prepare an editable narration manifest."""

import argparse
from collections import Counter
import os
from pathlib import Path

from meme_narration import BASE_DIR, SCRIPTS_PATH, NarrationLibrary, collect_images
from processing_log import ProcessingLog


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=BASE_DIR / "export")
    parser.add_argument("--scripts", type=Path, default=SCRIPTS_PATH)
    parser.add_argument("--recognizer", choices=["lmstudio", "tesseract"],
                        default=os.environ.get("MEME_RECOGNIZER", "lmstudio"))
    parser.add_argument("--refresh", "--refresh-ocr", action="store_true",
                        help="Повторить распознавание; записи source=manual сохраняются")
    args = parser.parse_args()
    log = ProcessingLog(args.scripts.parent / "logs", "narrations")
    log("RUN", "start", f"Подробный журнал: {log.path}", log_file=str(log.path))
    try:
        library = NarrationLibrary(args.scripts, args.input_dir, recognizer=args.recognizer, log=log)
        paths = collect_images(args.input_dir)
        library.prepare(paths, refresh=args.refresh)
        report = library.write_report()
        counts = Counter(library.entry(p)["status"] for p in paths)
        print(f"Мемов: {len(paths)}; статусы: {dict(counts)}")
        print(f"Тексты: {args.scripts}\nПросмотр: {report}")
    except (OSError, ValueError, RuntimeError) as exc:
        log("RUN", "error", str(exc), error=str(exc))
        parser.exit(1, f"Ошибка подготовки озвучки: {exc}\n")


if __name__ == "__main__":
    main()
