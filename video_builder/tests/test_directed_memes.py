from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from build_directed_memes import load_photos
from meme_direction import make_direction
from meme_number_speech import expand_numbers
from meme_prosody import alignment_language, select_windows
from meme_speech import CHANNELS, SAMPLE_RATE


class DirectedMemeTests(unittest.TestCase):
    def test_sender_is_read_from_bot_caption_and_direct_post(self):
        with tempfile.TemporaryDirectory() as folder:
            export = Path(folder)
            for name in ("a.jpg", "b.jpg"):
                (export / name).touch()
            messages = [
                {"photo": "a.jpg", "from": "SadUglyBot", "text_entities": [
                    {"type": "plain", "text": "👤"}, {"type": "code", "text": "Максим"},
                    {"type": "plain", "text": " Попытка №2"},
                ]},
                {"photo": "b.jpg", "from": "Daniil"},
                {"photo": "ignored.jpg", "text_entities": [{"type": "hashtag", "text": "#dobrokek"}]},
            ]
            (export / "result.json").write_text(json.dumps({"messages": messages}), encoding="utf-8")
            self.assertEqual(load_photos(export), [(export / "a.jpg", "Максим"), (export / "b.jpg", "Daniil")])

    def test_number_values_are_preserved_while_grammar_changes(self):
        examples = (
            ("до 140 дней", {}, "до ста сорока дней"),
            ("31 минута", {}, "тридцать одна минута"),
            ("о 4 друзьях", {1: {"case": "p"}}, "о четырёх друзьях"),
            ("285,972h 59m", {}, "двести восемьдесят пять тысяч девятьсот семьдесят два часа пятьдесят девять минут"),
            ("давление 280/130", {}, "давление двести восемьдесят на сто тридцать"),
            ("6в1", {}, "шесть в одном"),
            ("7-го июля", {}, "седьмого июля"),
            ("22 октября будет дискотека", {}, "двадцать второго октября будет дискотека"),
            ("в 5 классе", {}, "в пятом классе"),
            ("станки 1947 года", {}, "станки тысяча девятьсот сорок седьмого года"),
            ("для 6-8 классов", {}, "для шестых — восьмых классов"),
            ("с 17.00 до 18.30", {}, "с семнадцати часов до восемнадцати тридцати"),
            ("Safari — 1 час 24 минуты", {1: {"case": "g"}, 2: {"case": "g"}},
             "Safari — один час двадцать четыре минуты"),
            ("Galaxus — 31 минута", {1: {"case": "a"}}, "Galaxus — тридцать одна минута"),
            ("1 mins played recently", {}, "one minute played recently"),
            ("HELLDIVERS 2", {}, "HELLDIVERS two"),
            ("2 gün yem vermedik", {}, "iki gün yem vermedik"),
        )
        for text, forms, expected in examples:
            with self.subTest(text=text):
                spoken, edits = expand_numbers(text, forms)
                self.assertEqual(spoken, expected)
                self.assertTrue(edits)

    def test_alignment_language_does_not_read_ascii_i_as_turkish(self):
        self.assertEqual(alignment_language([{"text": "In-Game"}]), "en")
        self.assertEqual(alignment_language([{"text": "iki gün"}]), "tr")

    def test_model_selects_form_pause_and_word(self):
        with tempfile.TemporaryDirectory() as folder:
            photo = Path(folder) / "meme.png"
            Image.new("RGB", (16, 16)).save(photo)
            original = [{"text": "Мы?", "pause_after": 1.2, "voicestudio_duration": .7},
                        {"text": "Ждём 2 минуты."}, {"text": "Готово!"}]
            responses = [
                {"forms": [{"id": 1, "case": "a", "gender": "f", "kind": "cardinal"}]},
                {"default_pause": .35, "beats": [[1, .4], [2, .85]], "cues": [[4, "fall"]]},
            ]
            with patch("meme_direction.ask_json", side_effect=responses):
                plan = make_direction(object(), photo, original)
        self.assertEqual(plan["segments"][0]["pause_after"], 1.2)
        self.assertEqual(plan["segments"][1], {"text": "Ждём две минуты.", "pause_after": .85})
        self.assertEqual(plan["cues"][0]["word"], "минуты")
        pcm = np.full((5 * SAMPLE_RATE, CHANNELS), 1000, dtype=np.int16)
        words = [
            {"word": word, "start": start, "end": end, "attention": None}
            for word, start, end in [("Мы", .5, .8), ("Ждём", 2, 2.3), ("две", 2.4, 2.6), ("минуты", 2.7, 3.1)]
        ]
        windows = select_windows(plan, [(.45, 1), (2, 3.2), (4, 4.4)], words, pcm)
        self.assertEqual(len(windows), 1)
        self.assertGreater(windows[0]["from"], 2.6)
        self.assertLessEqual(windows[0]["to"], 3.2)
        self.assertEqual(windows[0]["mode"], "fall")

    def test_word_pause_splits_at_punctuation_and_remaps_later_cues(self):
        with tempfile.TemporaryDirectory() as folder:
            photo = Path(folder) / "meme.png"
            Image.new("RGB", (16, 16)).save(photo)
            original = [{"text": "Я пришёл вовремя, а он уже ушёл."}]
            response = {"holds": [[3, .75], [5, 1.5]], "cues": [[7, "fall"]]}
            with patch("meme_direction.ask_json", return_value=response):
                plan = make_direction(object(), photo, original)
        self.assertEqual([line["text"] for line in plan["segments"]], ["Я пришёл вовремя,", "а он уже ушёл."])
        self.assertEqual(plan["segments"][0]["pause_after"], .75)
        self.assertEqual(plan["cues"][0]["line"], 1)
        self.assertEqual(plan["cues"][0]["word"], "ушёл")


if __name__ == "__main__":
    unittest.main()
