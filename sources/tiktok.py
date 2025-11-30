import yt_dlp
import os
import re
from typing import Dict, Any
from datetime import datetime
from sources.source import Source

TIKTOK_PATTERN = r'https?://((?:vm|vt|www)\.)?tiktok\.com/.*'


class TikTokSource(Source):
    def supports(self, url: str) -> bool:
        return bool(re.match(TIKTOK_PATTERN, url))

    @staticmethod
    def progress_hook(d: Dict[str, Any]) -> None:
        if d['status'] == 'downloading':
            progress = d.get('_percent_str', 'N/A')
            speed = d.get('_speed_str', 'N/A')
            eta = d.get('_eta_str', 'N/A')
            print(f"Downloading: {progress} at {speed} ETA: {eta}", end='\r')
        elif d['status'] == 'finished':
            print("\nDownload completed, finalizing...")

    def get_filename(self) -> str:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"tiktok_{timestamp}.mp4"

    def download(self, url: str, download_path: str) -> str:
        filename = self.get_filename()
        output_path = os.path.join(download_path, filename)

        ydl_opts = {
            'outtmpl': output_path,
            'format': 'best',
            'noplaylist': True,
            'quiet': False,
            'progress_hooks': [self.progress_hook],
            # Use FireFox cookies for authentication
            'cookiesfrombrowser': ('chrome',),
            'extractor_args': {'tiktok': {'webpage_download': True}},
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            }
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
            print(f"\nVideo successfully downloaded: {output_path}")
            return output_path
        return None


if __name__ == "__main__":
    downloader = TikTokSource()
    video_url = "https://vm.tiktok.com/ZMAEJeTsg/"

    if not downloader.supports(video_url):
        print("link is not supported")
        exit(1)

    print(downloader.download(video_url, "downloads"))
