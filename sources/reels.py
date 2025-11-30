import instaloader
import re
from sources.source import Source

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

    def download(self, url: str, download_path: str) -> str:
        shortcode = self._extract_shortcode(url)
        if not shortcode:
            return ""

        loader = CustomInstaloader()

        post = instaloader.Post.from_shortcode(loader.context, shortcode)
        loader.download_post(post, target=download_path)

        if len(loader.saved_files) == 0:
            return ""
        
        video_files = [path for path in loader.saved_files if path.lower().endswith(".mp4")]

        if len(video_files) != 0:
            return video_files[0]

        return loader.saved_files[0]


if __name__ == "__main__":
    downloader = ReelsSource()
    video_url = "https://www.instagram.com/reel/DRZ0PzfjHsk/?utm_source=ig_web_copy_link"

    if not downloader.supports(video_url):
        print("link is not supported")
        exit(1)

    print(downloader.download(video_url, "downloads"))
