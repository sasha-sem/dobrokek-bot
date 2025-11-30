import instaloader
import re
from sources.source import Source
import os
from datetime import datetime
import requests

REELS_PATTERN = r"https?://(?:www\.)?instagram\.com/(?:reel|reels|p)/[A-Za-z0-9_-]+"
REELS_SHORTCODE_PATTERN = r"(?:reel|reels|p)/([A-Za-z0-9_-]+)"

class CustomInstaloader(instaloader.Instaloader):
    def __init__(self):
        super().__init__()
        self.saved_files = []
    def download_pic(self, filename, url, mtime):
        urlmatch = re.search('\\.[a-z0-9]*\\?', url)
        file_extension = url[-3:] if urlmatch is None else urlmatch.group(0)[1:-1]
        filepath = filename + '.' + file_extension
        self.saved_files.append(filepath)
        return super().download_pic(filename, url, mtime)
    


class ReelsSource(Source):
    def supports(self, url: str) -> bool:
        return bool(self._extract_shortcode(url))

    def _extract_shortcode(self, link: str):
        url_match = re.search(REELS_PATTERN, link)
        if not url_match:
            return None
        shortcode_match = re.search(REELS_SHORTCODE_PATTERN, url_match.group(0))
        return shortcode_match.group(1) if shortcode_match else None
    
    def get_filename(self) -> str:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"reels_{timestamp}.mp4"
    
    def download(self, url: str, download_path: str) -> str:
        shortcode = self._extract_shortcode(url)
        if not shortcode:
            return ""

        loader = CustomInstaloader()

        post = instaloader.Post.from_shortcode(loader.context, shortcode)

        video_url = post.video_url

        filename = self.get_filename()
        output_path = os.path.join(download_path, filename)

        response = requests.get(video_url, stream=True)
        if response.status_code == 200:
            with open(output_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            print(f"Downloaded reels {output_path}")
            return output_path
        else:
            Exception("Error getting reels", response)


if __name__ == "__main__":
    downloader = ReelsSource()
    video_url = "https://www.instagram.com/reel/DRZ0PzfjHsk/?utm_source=ig_web_copy_link"

    if not downloader.supports(video_url):
        print("link is not supported")
        exit(1)

    print(downloader.download(video_url, "downloads"))
