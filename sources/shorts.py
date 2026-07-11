from pytubefix import YouTube
from sources.source import Source
import re
from datetime import datetime
import os
from pathlib import Path

YOUTUBE_SHORTS_PATTERN = r"https?://(?:www\.)?(?:youtube\.com/shorts/|youtu\.be/)[A-Za-z0-9_-]+"
DEFAULT_YOUTUBE_TOKEN_FILE = "data/youtube_oauth.json"


class YouTubeAuthenticationRequired(RuntimeError):
    pass


class ShortsSource(Source):
    def supports(self, url: str) -> bool:
        return bool(re.match(YOUTUBE_SHORTS_PATTERN, url))

    def get_filename(self) -> str:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"shorts_{timestamp}.mp4"
    
    def download(self, url: str, download_path: str) -> str:
        token_file = os.environ.get("YOUTUBE_TOKEN_FILE", DEFAULT_YOUTUBE_TOKEN_FILE)
        if not Path(token_file).is_file():
            raise YouTubeAuthenticationRequired(
                f"YouTube OAuth token not found at {token_file}. "
                "Run `make youtube-auth` before downloading YouTube videos."
            )

        video = YouTube(
            url,
            use_oauth=True,
            allow_oauth_cache=True,
            token_file=token_file,
        )
        stream = video.streams.filter(
            progressive=True,
            file_extension='mp4'
        ).order_by('resolution').desc().first()

        if not stream:
            return ""

        filepath = stream.download(output_path=download_path, filename=self.get_filename(), max_retries=5)
        return filepath


if __name__ == "__main__":
    downloader = ShortsSource()
    video_url = "https://www.youtube.com/shorts/WGITueokFh4"

    if not downloader.supports(video_url):
        print("link is not supported")
        exit(1)

    print(downloader.download(video_url, "downloads"))
