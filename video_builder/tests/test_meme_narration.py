import asyncio
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
import wave

from PIL import Image

import build_clips
import build_video
import clip_encoding
from meme_narration import NarrationLibrary, image_hash, save_json
from meme_speech import speech, write_narration


class ScriptCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.image = self.root / "meme.png"
        Image.new("RGB", (100, 100), "white").save(self.image)
        self.library = NarrationLibrary(self.root / "scripts.json", self.root)

    def test_changed_image_replaces_cached_text_but_not_unchanged_manual_edits(self):
        self.library.data["memes"]["meme.png"] = {
            "image_sha256": image_hash(self.image), "source": "manual", "status": "ready",
            "segments": [{"text": "Исправленная реплика."}],
        }
        with patch("meme_narration.LMStudioVision", side_effect=AssertionError("Should use cache")):
            self.library.prepare([self.image], refresh=True)
        self.assertEqual(self.library.entry(self.image)["segments"][0]["text"], "Исправленная реплика.")
        Image.new("RGB", (100, 100), "black").save(self.image)
        with patch("meme_narration.LMStudioVision") as engine:
            engine.return_value.extract.return_value = {
                "segments": [{"text": "Другая картинка."}], "status": "ready",
            }
            self.library.prepare([self.image])
        self.assertEqual(self.library.entry(self.image)["segments"][0]["text"], "Другая картинка.")
        self.assertEqual(self.library.entry(self.image)["status"], "ready")

    def test_invalid_pause_is_rejected_before_synthesis(self):
        for pause in (-1, float("nan"), float("inf"), 11):
            with self.subTest(pause=pause), self.assertRaises(ValueError):
                asyncio.run(write_narration([{"text": "Привет", "pause_after": pause}],
                                            "friend-a", "+0%", "unused", self.root / "unused.wav", voicestudio=Mock()))
        self.assertFalse((self.root / "unused.wav").exists())

    def test_llm_failure_reason_reaches_clip_metadata(self):
        self.library.data["memes"]["meme.png"] = {
            "image_sha256": image_hash(self.image), "source": "manual", "status": "error",
            "segments": [], "error": "Extra data: line 1 column 111", "attempts": [{"attempt": 1, "status": "error"}],
        }
        audio, metadata = build_clips.prepare_meme_audio(
            str(self.image), self.root / "meme.wav", library=self.library, voice="friend-a", rate="+0%", voicestudio=Mock(),
        )
        self.assertIsNone(audio)
        self.assertEqual(metadata["error"], "Extra data: line 1 column 111")
        self.assertEqual(len(metadata["llm_attempts"]), 1)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
class NarratedClipTests(unittest.TestCase):
    def setUp(self):
        import imageio_ffmpeg
        self.ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        self.enc = {"ffmpeg_flags": ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]}
        self.temp = tempfile.TemporaryDirectory(prefix="dobrokek-voiceover-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.image = self.root / "meme.png"
        Image.new("RGB", (301, 499), "white").save(self.image)
        self.audio = self.root / "speech.wav"
        subprocess.run([
            self.ffmpeg, "-v", "error", "-f", "lavfi", "-i",
            "sine=frequency=440:sample_rate=48000:duration=8.21", "-ac", "2", str(self.audio),
        ], check=True)

    def probe(self, path):
        return json.loads(subprocess.check_output([
            "ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path),
        ]))

    def test_long_narration_is_not_cut_off_and_silent_meme_stays_five_seconds(self):
        for narrated, caption in ((True, "Автор"), (True, ""), (False, "Автор")):
            with self.subTest(narrated=narrated, caption=caption):
                output = self.root / f"clip-{narrated}-{bool(caption)}.mp4"
                with patch.object(clip_encoding, "get_encoder_config", return_value=self.enc), \
                     patch.dict(os.environ, {"FFMPEG_BINARY": self.ffmpeg}):
                    clip_encoding.process_meme_image(str(self.image), str(output), caption,
                                                    narration_path=self.audio if narrated else None)
                info = self.probe(output)
                self.assertAlmostEqual(float(info["format"]["duration"]), 8.233333 if narrated else 5, delta=0.05)
                video = next(s for s in info["streams"] if s["codec_type"] == "video")
                audio = next(s for s in info["streams"] if s["codec_type"] == "audio")
                self.assertEqual((video["width"], video["height"]), (1920, 1080))
                self.assertEqual((audio["codec_name"], audio["sample_rate"], audio["channels"]), ("aac", "48000", 2))
                subprocess.run(["ffmpeg", "-v", "error", "-i", str(output), "-f", "null", "-"], check=True)

    def test_synthesis_failure_keeps_meme_and_records_error(self):
        scripts = self.root / "scripts.json"
        save_json(scripts, {"version": 1, "memes": {"meme.png": {
            "image_sha256": image_hash(self.image), "source": "manual", "status": "ready",
            "segments": [{"text": "Первая реплика."}, {"text": "Вторая реплика."}],
        }}})
        result = self.root / "result.json"
        result.write_text(json.dumps({"messages": [{"photo": "meme.png", "text_entities": []}]}))
        temp = self.root / "clips"
        manifest = temp / "manifest.json"
        with patch.multiple(build_clips, INPUT_DIR=self.root, INPUT_FILE=result, TEMP_DIR=temp,
                            MANIFEST_PATH=manifest, FIRST_CLIP_PATH="missing", LAST_CLIP_PATH="missing"), \
             patch.object(clip_encoding, "get_encoder_config", return_value=self.enc), \
             patch.object(build_clips, "write_narration", new_callable=AsyncMock,
                          side_effect=RuntimeError("Service unavailable")) as synthesize, \
             patch.object(build_clips, "VoiceStudio", return_value=SimpleNamespace(
                 model="omnivoice", require_profiles=Mock())), \
             patch.dict(os.environ, {"FFMPEG_BINARY": self.ffmpeg}), \
             patch("sys.argv", ["build_clips.py", "--scripts", str(scripts), "--voicestudio-voices", "friend-a"]), \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            build_clips.main()
        data = json.loads(manifest.read_text())
        self.assertEqual(len(data["clips"]), 1)
        self.assertEqual(data["skipped"], [])
        self.assertEqual(data["clips"][0]["narration"]["status"], "tts_error")
        self.assertEqual(data["summary"]["tts"]["statuses"], {"tts_error": 1})
        self.assertIn("Service unavailable", data["summary"]["problems"][0]["error"])
        records = [json.loads(line) for line in Path(data["log_file"]).read_text().splitlines()]
        self.assertTrue(any(r["event"] == "narration_error" for r in records))
        self.assertTrue(any(r["stage"] == "SUMMARY" for r in records))
        self.assertEqual(synthesize.call_args.args[1], "friend-a")
        self.assertAlmostEqual(float(self.probe(Path(data["clips"][0]["path"]))["format"]["duration"]), 5, delta=0.05)

    def test_existing_voicestudio_cache_avoids_network(self):
        cache = self.root / "cache"
        cache.mkdir()
        studio = SimpleNamespace(url="http://localhost:3900/v1", model="omnivoice", num_step=32, save=AsyncMock())
        identity = json.dumps(["voicestudio", studio.url, studio.model, 32, "friend-a", "-8%", "Из кэша"],
                              ensure_ascii=False)
        key = hashlib.sha256(identity.encode()).hexdigest()
        shutil.copyfile(self.audio, cache / f"{key}.wav")
        pcm = asyncio.run(speech("Из кэша", "friend-a", "-8%", self.ffmpeg, cache_dir=cache, voicestudio=studio))
        studio.save.assert_not_awaited()
        self.assertGreater(len(pcm), 48000 * 4)

    def test_no_voiceover_skips_narration_services(self):
        export = self.root / "result.json"
        save_json(export, {"messages": [{"photo": self.image.name}]})
        temp = self.root / "clips"
        manifest = temp / "manifest.json"
        with (patch.multiple(build_clips, INPUT_DIR=self.root, INPUT_FILE=export, TEMP_DIR=temp,
                             MANIFEST_PATH=manifest, FIRST_CLIP_PATH="missing", LAST_CLIP_PATH="missing"),
              patch.object(clip_encoding, "get_encoder_config", return_value=self.enc),
              patch.dict(os.environ, {"FFMPEG_BINARY": self.ffmpeg}),
              patch("build_directed_memes.prepare_clips") as prepare,
              patch.object(build_clips, "NarrationLibrary") as library,
              patch.object(build_clips, "VoiceStudio") as studio,
              patch("sys.argv", ["build_clips.py", "--no-meme-voiceover", "--scripts",
                                 str(self.root / "scripts.json")]),
              contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO())):
            build_clips.main()
        prepare.assert_not_called()
        library.assert_not_called()
        studio.assert_not_called()
        data = json.loads(manifest.read_text())
        self.assertEqual(data["skipped"], [])
        (meme,) = data["clips"]
        self.assertEqual(meme["narration"]["status"], "disabled")
        self.assertAlmostEqual(float(self.probe(meme["path"])["format"]["duration"]), 5, delta=0.05)

    def test_final_assembly_keeps_narrated_memes_without_section_assets(self):
        clip = self.root / "narrated.mp4"
        with patch.object(clip_encoding, "get_encoder_config", return_value=self.enc), \
             patch.dict(os.environ, {"FFMPEG_BINARY": self.ffmpeg}):
            clip_encoding.process_meme_image(str(self.image), str(clip), narration_path=self.audio)
        manifest = self.root / "manifest.json"
        save_json(manifest, {"clips": [{"role": "meme", "path": str(clip)}]})
        output = self.root / "final" / "output.mp4"
        with patch.multiple(build_video, MANIFEST_PATH=manifest, OUTPUT_FILE=output,
                            ASSETS_DIR=self.root / "missing-assets"), \
             patch.object(build_video, "get_encoder_config", return_value=self.enc), \
             patch.dict(os.environ, {"FFMPEG_BINARY": shutil.which("ffmpeg")}), \
             patch("sys.argv", ["build_video.py"]), \
             contextlib.redirect_stdout(io.StringIO()):
            build_video.main()
        self.assertAlmostEqual(float(self.probe(output)["format"]["duration"]), 8.233333, delta=0.2)

    def test_full_episode_reuses_directed_meme_between_regular_clips(self):
        temp = self.root / "clips"
        directed = temp / "directed-memes"
        directed.mkdir(parents=True)
        meme = directed / "clip.mp4"
        raw = self.root / "raw.mp4"
        subprocess.run([self.ffmpeg, "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=320x240:r=30",
                        "-t", "0.5", "-c:v", "libx264", str(raw)], check=True)
        export = self.root / "result.json"
        video = {"media_type": "video_file", "file": raw.name,
                 "text_entities": [{"type": "code", "text": "Автор ролика"}]}
        save_json(export, {"messages": [video, {"photo": self.image.name}, video]})
        direction = {"voice": "author-profile", "applied": 1, "segments": [{"text": "Реплика."}]}
        prepared_memes = {"clips": [
            {"path": str(meme), "source": str(self.image), "role": "meme", "title": "Автор",
             "status": "ready", "direction": direction},
        ]}
        save_json(directed / "manifest.json", prepared_memes)
        manifest = temp / "manifest.json"
        with (patch.object(clip_encoding, "get_encoder_config", return_value=self.enc),
              patch.dict(os.environ, {"FFMPEG_BINARY": self.ffmpeg})):
            clip_encoding.process_meme_image(str(self.image), str(meme), narration_path=self.audio)
            for use_manifest in (True, False):
                with self.subTest(use_manifest=use_manifest):
                    args = ["build_clips.py", "--scripts", str(self.root / "scripts.json")]
                    if use_manifest:
                        args.extend(["--directed-memes-manifest", str(directed / "manifest.json")])
                    with (patch.multiple(build_clips, INPUT_DIR=self.root, INPUT_FILE=export, TEMP_DIR=temp,
                                         MANIFEST_PATH=manifest, FIRST_CLIP_PATH="missing", LAST_CLIP_PATH="missing"),
                          patch("build_directed_memes.prepare_clips", return_value=prepared_memes) as prepare,
                          patch.object(build_clips, "prepare_meme_audio", side_effect=AssertionError("Use prepared audio")),
                          patch("sys.argv", args),
                          contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO())):
                        build_clips.main()
                    self.assertEqual(prepare.call_count, int(not use_manifest))
                    data = json.loads(manifest.read_text())
                    self.assertEqual(data["skipped"], [])
                    self.assertEqual([row["role"] for row in data["clips"]], ["main", "meme", "main"])
                    self.assertEqual(data["clips"][1]["narration"]["voice"], "author-profile")
        episode_meme = Path(data["clips"][1]["path"])
        self.assertEqual(episode_meme, temp / "meme_0.mp4")
        self.assertEqual(episode_meme.read_bytes(), meme.read_bytes())
        self.assertEqual(data["clips"][1]["narration"]["direction"], direction)
        self.assertTrue((directed / "manifest.json").is_file())

        assets = self.root / "assets"
        assets.mkdir()
        (assets / "meme_pause.mp4").write_bytes(b"\n")
        output = self.root / "final" / "output.mp4"
        with (patch.multiple(build_video, MANIFEST_PATH=manifest, OUTPUT_FILE=output, ASSETS_DIR=assets),
              patch.object(build_video, "get_encoder_config", return_value=self.enc),
              patch("sys.argv", ["build_video.py"]), contextlib.redirect_stdout(io.StringIO())):
            build_video.main()
        self.assertAlmostEqual(float(self.probe(output)["format"]["duration"]), 9.23, delta=.25)


if __name__ == "__main__":
    unittest.main()
