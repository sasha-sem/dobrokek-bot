import os
from pathlib import Path

from pytubefix import YouTube

from sources.shorts import DEFAULT_YOUTUBE_TOKEN_FILE


DEFAULT_TEST_VIDEO_URL = "https://www.youtube.com/watch?v=aqz-KE-bpKQ"


def authenticate(token_file: Path, video_url: str) -> None:
    token_file.parent.mkdir(parents=True, exist_ok=True)
    backup_file = token_file.with_suffix(f"{token_file.suffix}.bak")

    if token_file.exists():
        token_file.replace(backup_file)

    try:
        video = YouTube(
            video_url,
            use_oauth=True,
            allow_oauth_cache=True,
            token_file=str(token_file),
        )
        video.streams

        if not token_file.is_file():
            raise RuntimeError("pytubefix did not create an OAuth token file")

        token_file.chmod(0o600)
    except Exception:
        token_file.unlink(missing_ok=True)
        if backup_file.exists():
            backup_file.replace(token_file)
        raise
    else:
        backup_file.unlink(missing_ok=True)


def main() -> None:
    token_file = Path(
        os.environ.get("YOUTUBE_TOKEN_FILE", DEFAULT_YOUTUBE_TOKEN_FILE)
    )
    video_url = os.environ.get("YOUTUBE_AUTH_TEST_URL", DEFAULT_TEST_VIDEO_URL)

    print("Starting YouTube OAuth authorization.")
    authenticate(token_file, video_url)
    print(f"YouTube OAuth token saved to {token_file}.")


if __name__ == "__main__":
    main()
