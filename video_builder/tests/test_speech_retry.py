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
    CHANNELS, SAMPLE_RATE, SAMPLE_WIDTH, VOICES, SpeechStats, SpeechSynthesisError,
    prepare_voicestudio_segments, speech, write_narration, write_narration_with_fallback,
)
from processing_log import ProcessingLog


class SpeechRetryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_failure_in_second_segment_restarts_whole_meme_with_other_voice(self):
        calls = []
        primary_pcm = b"\x01\x00\x01\x00" * SAMPLE_RATE
        backup_pcm = b"\x02\x00\x02\x00" * SAMPLE_RATE

        async def synthesize(text, voice, rate, ffmpeg, **kwargs):
            calls.append((text, voice))
            kwargs["stats"].requests += 1
            if voice == VOICES[0] and text == "Последняя реплика":
                raise SpeechSynthesisError("NoAudioReceived", kwargs["stats"])
            return primary_pcm if voice == VOICES[0] else backup_pcm

        output = self.root / "meme.wav"
        with patch("meme_speech.speech", side_effect=synthesize):
            result = asyncio.run(write_narration_with_fallback(
                [{"text": "Первая реплика"}, {"text": "Последняя реплика"}],
                VOICES[0], "+0%", "unused", output,
            ))
        self.assertEqual(calls, [("Первая реплика", VOICES[0]), ("Последняя реплика", VOICES[0]),
                                 ("Первая реплика", VOICES[1]), ("Последняя реплика", VOICES[1])])
        self.assertTrue(result.fallback_used)
        self.assertEqual(result.voice, VOICES[1])
        self.assertEqual(result.requested_voice, VOICES[0])
        with wave.open(str(output)) as wav:
            wav.readframes(round(0.45 * SAMPLE_RATE))
            self.assertEqual(wav.readframes(SAMPLE_RATE * 2), backup_pcm * 2)

    def test_both_voice_failures_preserve_existing_audio(self):
        output = self.root / "existing.wav"
        output.write_bytes(b"previous audio")
        voices = []

        async def fail(text, voice, rate, ffmpeg, **kwargs):
            voices.append(voice)
            raise SpeechSynthesisError("NoAudioReceived", kwargs["stats"])

        with patch("meme_speech.speech", side_effect=fail), self.assertRaisesRegex(SpeechSynthesisError, "Оба голоса"):
            asyncio.run(write_narration_with_fallback([{"text": "Реплика"}], VOICES[0], "+0%", "unused", output))
        self.assertEqual(voices, list(VOICES))
        self.assertEqual(output.read_bytes(), b"previous audio")
        self.assertEqual(list(self.root.glob("*.wav")), [output])

    def test_retries_are_logged_and_success_is_cached(self):
        class NoAudioReceived(Exception):
            pass

        attempt = 0

        async def save(path):
            nonlocal attempt
            attempt += 1
            if attempt < 3:
                raise NoAudioReceived("empty provider response")
            Path(path).write_bytes(b"fixture audio")

        fake_edge = SimpleNamespace(Communicate=Mock(return_value=SimpleNamespace(save=save)),
                                    exceptions=SimpleNamespace(NoAudioReceived=NoAudioReceived))
        stats = SpeechStats()
        log = ProcessingLog(self.root / "logs", "test")
        pcm = b"\x02\x00" * SAMPLE_RATE * CHANNELS
        with patch.dict("sys.modules", {"edge_tts": fake_edge}), \
             patch("meme_speech.decode_speech", return_value=pcm), \
             patch("meme_speech.asyncio.sleep", new_callable=AsyncMock), \
             contextlib.redirect_stdout(io.StringIO()):
            asyncio.run(speech("unique retry fixture", VOICES[0], "+0%", "unused", cache_dir=self.root / "cache",
                               stats=stats, log=log, image="meme.png"))
            fake_edge.Communicate.side_effect = AssertionError("Cache should avoid a request")
            asyncio.run(speech("unique retry fixture", VOICES[0], "+0%", "unused", cache_dir=self.root / "cache",
                               stats=stats, log=log, image="meme.png"))
        self.assertEqual((stats.requests, stats.retries, stats.failed_requests, stats.cache_hits), (3, 2, 2, 1))
        records = [json.loads(line) for line in log.path.read_text().splitlines()]
        self.assertEqual(sum(r["event"] == "request_error" for r in records), 2)
        self.assertEqual(records[-1]["event"], "cache_hit")
        self.assertIn("NoAudioReceived", next(r["error"] for r in records if r["event"] == "request_error"))

    def test_voicestudio_failure_does_not_switch_to_edge_voices(self):
        studio = SimpleNamespace(url="http://localhost:3900/v1", model="omnivoice", num_step=32,
                                 save=AsyncMock(side_effect=RuntimeError("no audio")))
        output = self.root / "meme.wav"
        with patch("meme_speech.asyncio.sleep", new_callable=AsyncMock), self.assertRaises(SpeechSynthesisError):
            asyncio.run(write_narration_with_fallback(
                [{"text": "Реплика"}], "friend-a", "+0%", "unused", output,
                cache_dir=self.root / "cache", voicestudio=studio,
            ))
        self.assertEqual([call.args[1] for call in studio.save.await_args_list], ["friend-a"] * 3)
        self.assertFalse(output.exists())
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
