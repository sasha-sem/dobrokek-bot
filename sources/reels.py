import instaloader
import re
from sources.source import Source
import os
from datetime import datetime
import requests

REELS_PATTERN = r"https?://(?:www\.)?instagram\.com/(?:reel|reels|p)/[A-Za-z0-9_-]+"
REELS_SHORTCODE_PATTERN = r"(?:reel|reels|p)/([A-Za-z0-9_-]+)"

class ReelsSource(Source):
    def supports(self, url: str) -> bool:
        return bool(self._extract_shortcode(url))

    def _extract_shortcode(self, link: str):
        url_match = re.search(REELS_PATTERN, link)
        if not url_match:
            return None
        shortcode_match = re.search(
            REELS_SHORTCODE_PATTERN, url_match.group(0))
        return shortcode_match.group(1) if shortcode_match else None

    def get_filename(self) -> str:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"reels_{timestamp}.mp4"

    def download(self, url: str, download_path: str) -> str:
        shortcode = self._extract_shortcode(url)
        if not shortcode:
            return ""

        video_url = f"https://www.kkinstagram.com/reel/{shortcode}/?igsh=MWlqc21yMGZkaDJoaA=="

        headers = {
            "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) Telegram-Android/11.7.3 (Samsung SM-A750F; Android 10; SDK 29; LOW)"
        }

        video_resp = requests.get(video_url, headers=headers, stream=True)
        video_resp.raise_for_status()

        ct = video_resp.headers.get("Content-Type", "")

        if ct != "video/mp4":
            Exception("Can't download this reel")

        filename = self.get_filename()
        output_path = os.path.join(download_path, filename)

        with open(output_path, "wb") as f:
            for chunk in video_resp.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    
        return output_path


if __name__ == "__main__":
    downloader = ReelsSource()
    video_url = "https://www.instagram.com/reel/DRZ0PzfjHsk/?utm_source=ig_web_copy_link"

    if not downloader.supports(video_url):
        print("link is not supported")
        exit(1)

    print(downloader.download(video_url, "downloads"))
