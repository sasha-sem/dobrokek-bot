import re
import os
import requests
from datetime import datetime
from sources.source import Source

TIKTOK_PATTERN = r'https?://(?:vm|vt)\.tiktok\.com/([A-Za-z0-9_-]+)'


class TikTokSource(Source):
    def _extract_code(self, url: str):
        m = re.search(TIKTOK_PATTERN, url)
        return m.group(1) if m else None

    def supports(self, url: str) -> bool:
        return bool(self._extract_code(url))

    def get_filename(self) -> str:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"tiktok_{timestamp}.mp4"

    def download(self, url: str, download_path: str) -> str:
        code = self._extract_code(url)
        if not code:
            return ""

        video_url = f"https://vt.kktiktok.com/{code}/"

        headers = {
            "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) Telegram-Android/11.7.3 (Samsung SM-A750F; Android 10; SDK 29; LOW)"
        }

        resp = requests.get(video_url, headers=headers, stream=True)
        resp.raise_for_status()

        if resp.headers.get("Content-Type", "") != "video/mp4":
            raise Exception("Can't download this tiktok")

        output_path = os.path.join(download_path, self.get_filename())

        with open(output_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)

        return output_path


if __name__ == "__main__":
    downloader = TikTokSource()
    video_url = "https://vm.tiktok.com/ZMAEJeTsg/"

    if not downloader.supports(video_url):
        print("link is not supported")
        exit(1)

    print(downloader.download(video_url, "downloads"))
