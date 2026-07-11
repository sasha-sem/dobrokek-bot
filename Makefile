.PHONY: youtube-auth

youtube-auth:
	docker compose run --rm --build bot python -m scripts.youtube_auth
