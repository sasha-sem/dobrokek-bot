from pytubefix import YouTube
from sources.source import Source
import re


YOUTUBE_SHORTS_PATTERN = r"https?://(?:www\.)?(?:youtube\.com/shorts/|youtu\.be/)[A-Za-z0-9_-]+"


class ShortsSource(Source):
    def supports(self, url: str) -> bool:
        return bool(re.match(YOUTUBE_SHORTS_PATTERN, url))

    def download(self, url: str, download_path: str) -> str:
        video = YouTube(url)
        stream = video.streams.filter(
            progressive=True,
            file_extension='mp4'
        ).order_by('resolution').desc().first()

        if not stream:
            return ""

        filepath = stream.download(output_path=download_path)
        return filepath


if __name__ == "__main__":
    downloader = ShortsSource()
    video_url = "https://www.youtube.com/shorts/WGITueokFh4"

    if not downloader.supports(video_url):
        print("link is not supported")
        exit(1)

    print(downloader.download(video_url, "downloads"))
