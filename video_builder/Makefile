PYTHON := poetry run python
EP ?= $(error Укажи номер выпуска: make assets EP=11)

.PHONY: install assets clips video all clean

install:
	poetry install

assets:
	$(PYTHON) build_assets.py --episode $(EP)

clips:
	$(PYTHON) build_clips.py

video:
	$(PYTHON) build_video.py

all: assets clips video

clean:
	rm -rf temp_clips/ result/
