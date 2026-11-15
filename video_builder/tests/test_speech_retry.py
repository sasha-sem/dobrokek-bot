import asyncio
import contextlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch
import wave

from meme_speech import (
    CHANNELS, SAMPLE_RATE, SAMPLE_WIDTH, SpeechStats,
    prepare_voicestudio_segments, speech, write_narration,
)
from processing_log import ProcessingLog


class SpeechRetryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_retries_are_logged_and_success_is_cached(self):
        attempt = 0

        async def save(text, voice, rate, path, *, duration=None):
            nonlocal attempt
            attempt += 1
            if attempt < 3:
                raise RuntimeError("empty provider response")
            path.write_bytes(b"fixture audio")

        studio = SimpleNamespace(url="http://localhost:3900/v1", model="omnivoice", num_step=32,
                                 save=AsyncMock(side_effect=save))
        stats = SpeechStats()
        log = ProcessingLog(self.root / "logs", "test")
        pcm = b"\x02\x00" * SAMPLE_RATE * CHANNELS
        with patch("meme_speech.decode_speech", return_value=pcm), \
             patch("meme_speech.asyncio.sleep", new_callable=AsyncMock), \
             contextlib.redirect_stdout(io.StringIO()):
            asyncio.run(speech("unique retry fixture", "friend-a", "+0%", "unused", cache_dir=self.root / "cache", voicestudio=studio,
                               stats=stats, log=log, image="meme.png"))
            studio.save.side_effect = AssertionError("Cache should avoid a request")
            asyncio.run(speech("unique retry fixture", "friend-a", "+0%", "unused", cache_dir=self.root / "cache", voicestudio=studio,
                               stats=stats, log=log, image="meme.png"))
        self.assertEqual((stats.requests, stats.retries, stats.failed_requests, stats.cache_hits), (3, 2, 2, 1))
        records = [json.loads(line) for line in log.path.read_text().splitlines()]
        self.assertEqual(sum(r["event"] == "request_error" for r in records), 2)
        self.assertEqual(records[-1]["event"], "cache_hit")
        self.assertIn("empty provider response", next(r["error"] for r in records if r["event"] == "request_error"))

    def test_failed_retries_preserve_existing_audio(self):
        studio = SimpleNamespace(url="http://localhost:3900/v1", model="omnivoice", num_step=32,
                                 save=AsyncMock(side_effect=RuntimeError("no audio")))
        output = self.root / "meme.wav"
        output.write_bytes(b"previous audio")
        with patch("meme_speech.asyncio.sleep", new_callable=AsyncMock), self.assertRaisesRegex(RuntimeError, "no audio"):
            asyncio.run(write_narration(
                [{"text": "Реплика"}], "friend-a", "+0%", "unused", output,
                cache_dir=self.root / "cache", voicestudio=studio,
            ))
        self.assertEqual([call.args[1] for call in studio.save.await_args_list], ["friend-a"] * 3)
        self.assertEqual(output.read_bytes(), b"previous audio")
        self.assertEqual(list(self.root.glob("*.wav")), [output])
        self.assertEqual(list((self.root / "cache").iterdir()), [])

    def test_voicestudio_preserves_questions_and_creates_pauses_between_utterances(self):
        segments = [
            {"text": "Мы?"},
            {"text": "Я понял,"},
            {"text": "что происходит", "pause_after": 0.8},
            {"text": "Точно"},
        ]
        self.assertEqual(prepare_voicestudio_segments(segments), [
            {"text": "Мы?", "pause_after": 0.32},
            {"text": "Я понял, что происходит.", "pause_after": 0.8},
            {"text": "Точно.", "pause_after": 0.0},
        ])

    def test_voicestudio_question_duration_and_pause_are_applied_separately(self):
        output = self.root / "question.wav"
        pcm = b"\x01\x00" * CHANNELS * 4
        segments = [{"text": "Мы?", "voicestudio_duration": 0.7, "pause_after": 1.2},
                    {"text": "Ответ."}]
        with patch("meme_speech.speech", new_callable=AsyncMock, return_value=pcm) as synthesize:
            asyncio.run(write_narration(segments, "friend-a", "-8%", "unused", output, voicestudio=Mock()))
        self.assertEqual(synthesize.await_args_list[0].kwargs["voicestudio_duration"], 0.7)
        self.assertIsNone(synthesize.await_args_list[1].kwargs["voicestudio_duration"])
        with wave.open(str(output)) as wav:
            wav.setpos(round(0.45 * SAMPLE_RATE))
            self.assertEqual(wav.readframes(4), pcm)
            pause_frames = round(1.2 * SAMPLE_RATE)
            self.assertEqual(wav.readframes(pause_frames), b"\0" * pause_frames * CHANNELS * SAMPLE_WIDTH)
            self.assertEqual(wav.readframes(4), pcm)


if __name__ == "__main__":
    unittest.main()
