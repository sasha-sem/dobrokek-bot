PYTHON := poetry run python
EP ?= $(error Укажи номер выпуска: make assets EP=11)

# Флаги ассетов для `make assets`. Закомментируй (#) строку, чтобы НЕ генерить этот ассет.
# ASSET_INTRO       := --intro
ASSET_OUTRO       := --outro
# ASSET_MEME_PAUSE  := --meme-pause
ASSET_MEME_HEROES := --meme-heroes

ASSET_FLAGS := $(ASSET_INTRO) $(ASSET_OUTRO) $(ASSET_MEME_PAUSE) $(ASSET_MEME_HEROES)

.PHONY: install assets clips video all clean

install:
	poetry install

assets:
	$(PYTHON) build_assets.py --episode $(EP) $(ASSET_FLAGS)

clips:
	$(PYTHON) build_clips.py

video:
	$(PYTHON) build_video.py

all: assets clips video

clean:
	rm -rf temp_clips/ result/
