import base64
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from urllib.error import URLError
from unittest.mock import patch

from PIL import Image

from meme_narration import NarrationLibrary, image_hash
from meme_vision import LMStudioVision, parse_result


MODEL = "qwen/qwen3.5-4b"


class FakeLMStudio:
    def __init__(self):
        self.models = [{"key": MODEL, "type": "llm", "capabilities": {"vision": True},
                        "loaded_instances": [{"id": "loaded-vision"}]}]
        self.finish_reason = "stop"
        self.payloads = []
        self.native_results = []
        self.result = {"has_text": True, "needs_review": False,
                       "segments": [{"text": "Первая реплика.", "pause_after": 0.65},
                                    {"text": "Последняя реплика!", "pause_after": 0}]}

    def open(self, request, timeout):
        if request.full_url.endswith("/api/v1/models"):
            result = {"models": self.models}
        elif request.full_url.endswith("/api/v1/models/load"):
            self.payloads.append(json.loads(request.data))
            result = {"instance_id": "loaded-vision", "status": "loaded"}
        elif request.full_url.endswith("/api/v1/chat"):
            self.payloads.append(json.loads(request.data))
            result = {"output": [{"type": "message", "content": json.dumps(self.result)}],
                      "stats": {"total_output_tokens": 100, "reasoning_output_tokens": 0}}
            if self.native_results:
                result = self.native_results.pop(0)
        else:
            self.payloads.append(json.loads(request.data))
            result = {"choices": [{"finish_reason": self.finish_reason,
                                   "message": {"content": json.dumps(self.result)}}]}
        return io.BytesIO(json.dumps(result).encode())


class VisionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.image = self.root / "meme.png"
        Image.new("RGB", (350, 142), "white").save(self.image)
        self.fake = FakeLMStudio()
        self.opener = patch("meme_vision.build_opener", return_value=self.fake)
        self.opener.start()
        self.addCleanup(self.opener.stop)
        self.env = patch.dict(os.environ, {"LM_STUDIO_URL": "http://127.0.0.1:1234",
                                           "LM_STUDIO_MODEL": "", "LM_STUDIO_API_TOKEN": ""})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_image_request_and_script_round_trip(self):
        model = LMStudioVision()
        result = model.extract(self.image)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["model"], MODEL)
        self.assertEqual(result["segments"][-1]["text"], "Последняя реплика!")
        request = self.fake.payloads[0]
        self.assertEqual(request["model"], "loaded-vision")
        self.assertEqual(request["response_format"]["type"], "json_schema")
        image_url = request["messages"][1]["content"][1]["image_url"]["url"]
        self.assertTrue(image_url.startswith("data:image/png;base64,"))
        with Image.open(io.BytesIO(base64.b64decode(image_url.split(",", 1)[1]))) as image:
            self.assertEqual(image.size, (350, 142))

    def test_text_only_model_is_rejected_before_any_image_is_sent(self):
        self.fake.models[0]["capabilities"]["vision"] = False
        with self.assertRaises(RuntimeError):
            LMStudioVision()
        self.assertEqual(self.fake.payloads, [])

    def test_native_api_disables_reasoning_for_capable_models(self):
        self.fake.models[0]["capabilities"]["reasoning"] = {"allowed_options": ["off", "on"]}
        result = LMStudioVision().extract(self.image)
        self.assertEqual(result["status"], "ready")
        payload = self.fake.payloads[0]
        self.assertEqual(payload["reasoning"], "off")
        self.assertFalse(payload["store"])
        self.assertEqual(payload["input"][1]["type"], "image")

    def test_invalid_json_retries_with_a_correction_prompt(self):
        self.fake.models[0]["capabilities"]["reasoning"] = {"allowed_options": ["off", "on"]}
        self.fake.native_results = [{"output": [{"type": "message", "content": '{} trailing text'}]}]
        with patch("meme_vision.time.sleep"):
            result = LMStudioVision().extract(self.image)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(len(self.fake.payloads), 2)
        self.assertEqual(result["attempts"][0]["code"], "invalid_json")
        self.assertIn("Предыдущая попытка", self.fake.payloads[1]["system_prompt"])

    def test_truncation_retries_with_larger_output_budget(self):
        self.fake.models[0]["capabilities"]["reasoning"] = {"allowed_options": ["off", "on"]}
        self.fake.native_results = [{"output": [{"type": "message", "content": '{}'}],
                                    "stats": {"total_output_tokens": 2048}}]
        with patch("meme_vision.time.sleep"):
            result = LMStudioVision().extract(self.image)
        self.assertEqual(result["status"], "ready")
        self.assertEqual([p["max_output_tokens"] for p in self.fake.payloads], [2048, 4096])

    def test_exhausted_retries_store_the_reason_and_attempt_history(self):
        self.fake.models[0]["capabilities"]["reasoning"] = {"allowed_options": ["off", "on"]}
        self.fake.native_results = [{"output": [{"type": "message", "content": '{} trailing text'}]}] * 3
        library = NarrationLibrary(self.root / "scripts.json", self.root)
        with patch("meme_vision.time.sleep"):
            library.prepare([self.image])
        entry = library.entry(self.image)
        self.assertEqual(entry["status"], "error")
        self.assertIn("Extra data", entry["error"])
        self.assertEqual(len(entry["attempts"]), 3)
        self.assertEqual((library.summary["requests"], library.summary["retries"], library.summary["errors"]), (3, 2, 1))

    def test_json_fence_is_accepted_but_trailing_prose_is_not(self):
        content = json.dumps(self.fake.result)
        self.assertEqual(parse_result('```json\n' + content + '\n```')["status"], "ready")
        with self.assertRaises(ValueError):
            parse_result(content + " another answer")

    def test_only_unambiguous_closing_format_defects_are_repaired(self):
        content = json.dumps(self.fake.result)
        self.assertEqual(parse_result(content + '`')["format_repaired"], "trailing_markdown_marker")
        self.assertEqual(parse_result(content[:-1])["format_repaired"], "missing_final_brace")
        for invalid in (content + content, content[:-5], content + " text", content + "}"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                parse_result(invalid)

    def test_unloaded_model_gets_bounded_context_before_inference(self):
        self.fake.models[0]["loaded_instances"] = []
        LMStudioVision().extract(self.image)
        self.assertEqual(self.fake.payloads[0]["context_length"], 8192)
        self.assertFalse(self.fake.payloads[0]["offload_kv_cache_to_gpu"])

    def test_wsl_can_use_windows_localhost_with_request_body_on_stdin(self):
        models = json.dumps({"models": self.fake.models}).encode() + b"\n200"
        response = json.dumps({"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps(self.fake.result)}}]}).encode() + b"\n200"
        with patch.object(self.fake, "open", side_effect=URLError("Windows localhost")), \
             patch("meme_vision.platform.release", return_value="microsoft-standard-WSL2"), \
             patch("meme_vision.shutil.which", return_value="/mnt/c/Windows/System32/curl.exe"), \
             patch("meme_vision.subprocess.run") as execute:
            execute.side_effect = [
                type("Response", (), {"returncode": 0, "stdout": models})(),
                type("Response", (), {"returncode": 0, "stdout": response})(),
            ]
            result = LMStudioVision().extract(self.image)
        self.assertEqual(result["status"], "ready")
        command = execute.call_args.args[0]
        self.assertIn("@-", command)
        payload = json.loads(execute.call_args.kwargs["input"])
        self.assertTrue(payload["messages"][1]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,"))

    def test_truncated_answer_is_not_accepted(self):
        self.fake.finish_reason = "length"
        with self.assertRaisesRegex(ValueError, "не завершила"):
            LMStudioVision()._extract_once(self.image)

    def test_visual_meme_and_uncertain_text_stay_silent(self):
        result = parse_result(json.dumps({"has_text": False, "needs_review": False, "segments": []}))
        self.assertEqual(result["status"], "no_text")
        result = parse_result(json.dumps({"has_text": True, "needs_review": True, "segments": []}))
        self.assertEqual(result["status"], "review")
        with self.assertRaises(ValueError):
            parse_result(json.dumps({"has_text": False, "needs_review": False, "segments": [{"text": "Выдумано"}]}))

    def test_switching_to_vision_replaces_ocr_draft_and_reuses_result_offline(self):
        library = NarrationLibrary(self.root / "scripts.json", self.root)
        library.data["memes"]["meme.png"] = {
            "image_sha256": image_hash(self.image), "source": "ocr", "status": "review",
            "segments": [{"text": "Ошибки OCR"}],
        }
        library.prepare([self.image])
        self.assertEqual(library.entry(self.image)["source"], "lmstudio")
        self.assertEqual(library.entry(self.image)["status"], "ready")
        self.assertEqual(len(self.fake.payloads), 1)
        library = NarrationLibrary(library.path, self.root)
        with patch("meme_narration.LMStudioVision", side_effect=AssertionError("Use cached script")):
            library.prepare([self.image])
        self.assertEqual(len(self.fake.payloads), 1)


if __name__ == "__main__":
    unittest.main()
