import contextlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from audio_normalization import (
    MAX_GAIN_DB,
    TRUE_PEAK_DB,
    measure_loudness,
    normalization_gain,
)
import build_video

FFMPEG = "ffmpeg"


class GainTests(unittest.TestCase):
    def test_gain_targets_loudness(self):
        self.assertAlmostEqual(normalization_gain(-30, -3), 14)
        self.assertAlmostEqual(normalization_gain(-12, -1), -4)

    def test_noise_and_silence_are_not_amplified(self):
        self.assertEqual(normalization_gain(-60, -40), 0)
        self.assertEqual(normalization_gain(-70, float("-inf")), 0)
        self.assertLessEqual(normalization_gain(-70, 0), 0)

    def test_gain_is_capped(self):
        self.assertEqual(normalization_gain(-49, -40), MAX_GAIN_DB)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
class FFmpegTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture_ffmpeg = os.environ.get("FFMPEG_TEST_ENCODER", FFMPEG)
        encoders = subprocess.check_output([cls.fixture_ffmpeg, "-hide_banner", "-encoders"], text=True)
        if "libx264" not in encoders:
            try:
                import imageio_ffmpeg
            except ImportError:
                raise unittest.SkipTest("Для тестового видео нужен libx264 или imageio-ffmpeg")
            cls.fixture_ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        cls.temp = tempfile.TemporaryDirectory(prefix="dobrokek-audio-test-")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name)
        cls.enc = {"ffmpeg_flags": ["-c:v", "libx264", "-preset", "ultrafast"]}
        cls.base = cls.root / "base.mp4"
        cls.run_ffmpeg(
            "-f", "lavfi", "-i", "color=size=1920x1080:rate=30:duration=2",
            "-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=48000:duration=2",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-ac", "2", str(cls.base),
        )

    @classmethod
    def run_ffmpeg(cls, *args):
        binary = cls.fixture_ffmpeg if "libx264" in args else FFMPEG
        return subprocess.run(
            [binary, "-y", "-hide_banner", "-loglevel", "error", *args],
            check=True, capture_output=True, text=True,
        )

    def prepare(self, source, name, normalize=True):
        target = self.root / name
        with contextlib.redirect_stdout(io.StringIO()):
            build_video.prepare_ts(FFMPEG, "ffprobe", str(source), target, self.enc,
                                   normalize_audio=normalize)
        return target

    def test_loud_and_quiet_clips_match_and_video_is_unchanged(self):
        levels = []
        for label, volume in (("quiet", -10), ("loud", 12)):
            source = self.root / f"{label}.mp4"
            self.run_ffmpeg("-i", str(self.base), "-c:v", "copy", "-af",
                            f"volume={volume}dB", "-c:a", "aac", str(source))
            target = self.prepare(source, f"{label}.ts")
            loudness, peak = measure_loudness(FFMPEG, str(target))
            self.assertAlmostEqual(loudness, -16, delta=0.3)
            self.assertLessEqual(peak, TRUE_PEAK_DB + 0.5)
            levels.append(loudness)
            hashes = [self.run_ffmpeg("-i", str(p), "-map", "0:v:0", "-f", "hash", "-").stdout
                      for p in (source, target)]
            self.assertEqual(*hashes)
        self.assertLess(abs(levels[0] - levels[1]), 0.3)

    def test_silent_and_missing_audio(self):
        for label, audio_args in (("silent", ["-af", "volume=0", "-c:a", "aac"]),
                                  ("missing", ["-an"])):
            source = self.root / f"{label}.mp4"
            self.run_ffmpeg("-i", str(self.base), "-c:v", "copy", *audio_args, str(source))
            target = self.prepare(source, f"{label}.ts")
            loudness, peak = measure_loudness(FFMPEG, str(target))
            self.assertLess(loudness, -60)
            self.assertEqual(peak, float("-inf"))
            probe = subprocess.check_output(
                ["ffprobe", "-v", "error", "-select_streams", "a:0",
                 "-show_entries", "stream=codec_name,sample_rate,channels",
                 "-of", "json", str(target)], text=True,
            )
            info = json.loads(probe)["streams"][0]
            self.assertEqual(info["codec_name"], "aac")
            self.assertEqual(info["sample_rate"], "48000")
            self.assertEqual(info["channels"], 2)

    def test_mono_measurement_matches_output(self):
        source = self.root / "mono.mp4"
        self.run_ffmpeg("-i", str(self.base), "-c:v", "copy", "-ac", "1",
                        "-ar", "44100", "-c:a", "aac", str(source))
        target = self.prepare(source, "mono.ts")
        self.assertAlmostEqual(measure_loudness(FFMPEG, str(target))[0], -16, delta=0.3)

    def test_transients_are_limited_without_leaving_the_clip_quiet(self):
        source = self.root / "transients.mp4"
        self.run_ffmpeg("-i", str(self.base), "-c:v", "copy", "-af",
                        r"aeval=val(0)*if(lt(mod(t\,1)\,0.005)\,6\,0.3):c=same",
                        "-c:a", "aac", "-b:a", "192k", str(source))
        target = self.prepare(source, "transients.ts")
        loudness, peak = measure_loudness(FFMPEG, str(target))
        self.assertGreater(loudness, -20)
        self.assertGreater(loudness - measure_loudness(FFMPEG, str(source))[0], 8)
        self.assertLessEqual(peak, TRUE_PEAK_DB + 0.5)
        durations = [float(subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(p)], text=True)) for p in (source, target)]
        self.assertAlmostEqual(*durations, delta=0.1)

    def test_final_assembly_and_opt_out(self):
        manifest = self.root / "manifest.json"
        manifest.write_text(json.dumps({"clips": [{"path": str(self.base), "role": "main"}] * 3}))
        for disabled in (False, True):
            output = self.root / f"result-{disabled}" / "output.mp4"
            with patch.multiple(build_video, MANIFEST_PATH=manifest, OUTPUT_FILE=output,
                                ASSETS_DIR=self.root / "no-assets"), \
                 patch.object(build_video, "get_encoder_config", return_value=self.enc), \
                 patch.dict(os.environ, {"FFMPEG_BINARY": FFMPEG}), \
                 patch("sys.argv", ["build_video.py"] + (["--no-normalize-audio"] if disabled else [])), \
                 contextlib.redirect_stdout(io.StringIO()):
                build_video.main()
            expected = measure_loudness(FFMPEG, str(self.base))[0] if disabled else -16
            self.assertAlmostEqual(measure_loudness(FFMPEG, str(output))[0], expected, delta=0.3)
            self.assertEqual(list(self.root.glob(".concat-*")), [])
            duration = float(subprocess.check_output(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "csv=p=0", str(output)], text=True))
            self.assertAlmostEqual(duration, 6, delta=0.2)


if __name__ == "__main__":
    unittest.main()
