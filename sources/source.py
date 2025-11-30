from abc import ABC, abstractmethod


class Source(ABC):
    @abstractmethod
    def supports(self, url: str) -> bool:
        pass

    @abstractmethod
    def download(self, url: str, download_path: str) -> str:
        pass
