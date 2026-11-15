"""Client for VoiceStudio's local speech API."""

import asyncio
from http.client import HTTPException
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener


DEFAULT_VOICESTUDIO_RATE = "-8%"


def speech_speed(rate: str) -> float:
    if not re.fullmatch(r"[+-][0-9]{1,3}%", rate):
        raise ValueError("Темп VoiceStudio задаётся в процентах, например +10%.")
    speed = (100 + int(rate[:-1])) / 100
    if not 0.25 <= speed <= 4.0:
        raise ValueError("Темп VoiceStudio должен быть от -75% до +300%.")
    return speed


class VoiceStudio:
    def __init__(self) -> None:
        url = os.environ.get("VOICESTUDIO_BASE_URL", "http://127.0.0.1:3900/v1").rstrip("/")
        parts = urlsplit(url)
        if (parts.scheme not in ("http", "https") or not parts.hostname or parts.username is not None
                or parts.query or parts.fragment):
            raise ValueError("VOICESTUDIO_BASE_URL должен быть HTTP(S)-адресом API VoiceStudio.")
        self.url = url if parts.path.endswith("/v1") else url + "/v1"
        self.model = os.environ.get("VOICESTUDIO_MODEL", "omnivoice")
        self.num_step = 32 if self.model == "omnivoice" else None
        self.headers = {"Content-Type": "application/json"}
        if token := os.environ.get("VOICESTUDIO_API_KEY"):
            self.headers["Authorization"] = f"Bearer {token}"
        self.opener = build_opener(ProxyHandler({}))
        self.is_wsl_loopback = ("microsoft" in platform.release().lower()
                                and parts.hostname in ("localhost", "127.0.0.1", "::1"))
        self.windows_curl = None

    def windows_request(self, url: str, body: bytes | None, timeout: int) -> bytes:
        command = [self.windows_curl, "--disable", "--config", "-", "--noproxy", "*",
                   "--silent", "--connect-timeout", "5", "--max-time", str(timeout),
                   "--write-out", "\n%{http_code}", "--url", url]
        # Keep authorization and meme text out of the process arguments.
        config = [f"header = {json.dumps(f'{key}: {value}', ensure_ascii=False)}"
                  for key, value in self.headers.items()]
        if body is not None:
            config.append(f"data-binary = {json.dumps(body.decode(), ensure_ascii=False)}")
        try:
            result = subprocess.run(command, input=("\n".join(config) + "\n").encode(),
                                    capture_output=True, timeout=timeout + 5)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("VoiceStudio не ответил вовремя.") from exc
        content, _, status = result.stdout.rpartition(b"\n")
        if result.returncode:
            raise RuntimeError(f"Не удалось обратиться к VoiceStudio ({self.url}), код curl {result.returncode}.")
        if not status.startswith(b"2"):
            raise RuntimeError(f"VoiceStudio вернул HTTP {status.decode(errors='replace')}.")
        return content

    def request(self, path: str, payload: dict | None = None) -> bytes:
        url = self.url + path
        body = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
        timeout = 180 if payload is not None else 5
        if self.windows_curl:
            return self.windows_request(url, body, timeout)
        try:
            with self.opener.open(Request(url, data=body, headers=self.headers), timeout=timeout) as response:
                return response.read()
        except HTTPError as exc:
            raise RuntimeError(f"VoiceStudio вернул HTTP {exc.code} для {path}.") from exc
        except URLError as exc:
            # When WSL cannot see Windows localhost, discover the transport with a GET before synthesis.
            if payload is None and self.is_wsl_loopback:
                if windows_curl := shutil.which("curl.exe"):
                    self.windows_curl = windows_curl
                    return self.windows_request(url, body, timeout)
            raise RuntimeError(f"Не удалось подключиться к VoiceStudio ({self.url}). Проверьте, что приложение запущено.") from exc
        except (OSError, HTTPException) as exc:
            raise RuntimeError(f"Ответ VoiceStudio прерван ({self.url}): {type(exc).__name__}.") from exc

    def require_profiles(self, voices: tuple[str, ...]) -> None:
        try:
            profiles = {entry["voice_id"] for entry in json.loads(self.request("/audio/voices"))["voices"]
                        if entry.get("type") == "profile"}
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise RuntimeError("VoiceStudio вернул некорректный список голосовых профилей.") from exc
        missing = [voice for voice in voices if voice not in profiles]
        if missing:
            raise ValueError("Голосовые профили VoiceStudio не найдены: " + ", ".join(missing))

    async def save(self, text: str, voice: str, rate: str, output: Path,
                   *, duration: float | None = None) -> None:
        payload = {
            "model": self.model, "voice": voice, "input": text,
            "response_format": "wav", "speed": speech_speed(rate),
        }
        if self.num_step is not None:
            payload["num_step"] = self.num_step
        if duration is not None:
            payload["duration"] = duration
        audio = await asyncio.to_thread(self.request, "/audio/speech", payload)
        output.write_bytes(audio)
