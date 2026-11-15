PYTHON := poetry run python
VIDEO_FLAGS ?=
CLIPS_FLAGS ?=
NARRATION_FLAGS ?=
DIRECTED_FLAGS ?=
EP ?= $(error Укажи номер выпуска: make assets EP=11)

# Флаги ассетов для `make assets`. Закомментируй (#) строку, чтобы НЕ генерить этот ассет.
ASSET_INTRO       := --intro
ASSET_OUTRO       := --outro
# ASSET_MEME_PAUSE  := --meme-pause
ASSET_MEME_HEROES := --meme-heroes

ASSET_FLAGS := $(ASSET_INTRO) $(ASSET_OUTRO) $(ASSET_MEME_PAUSE) $(ASSET_MEME_HEROES)

.PHONY: install assets narrations directed-memes clips video all clean

install:
	poetry install

assets:
	$(PYTHON) build_assets.py --episode $(EP) $(ASSET_FLAGS)

narrations:
	$(PYTHON) build_narrations.py $(NARRATION_FLAGS)

directed-memes:
	$(PYTHON) build_directed_memes.py $(DIRECTED_FLAGS)

clips:
	$(PYTHON) build_clips.py $(CLIPS_FLAGS)

video:
	$(PYTHON) build_video.py $(VIDEO_FLAGS)

all: assets
	$(MAKE) clips
	$(MAKE) video

clean:
	rm -rf temp_clips/ result/
