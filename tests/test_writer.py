"""Writer provider contracts are mocked; never call paid services."""
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import markup
import writer


class WriterTests(unittest.TestCase):
    def test_default_gemini_header_and_model_metadata(self):
        response = io.StringIO(json.dumps({"modelVersion": "gemini-reported", "candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "private reasoning", "thought": True}, {"text": "answer"}]}}]}))
        opener = mock.Mock()
        opener.open.return_value = response
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "fake-direct", "OPENROUTER_API_KEY": "fake-router"}, clear=True), mock.patch.object(writer.urllib.request, "build_opener", return_value=opener):
            client = writer.Client("gemini", writer.DEFAULT_MODEL)
            self.assertEqual(client.generate("fictional"), "answer")
        request = opener.open.call_args.args[0]
        self.assertEqual(request.headers["X-goog-api-key"], "fake-direct")
        self.assertNotIn("Authorization", request.headers)
        self.assertTrue(request.full_url.endswith("/gemini-2.5-pro:generateContent"))
        self.assertEqual(client.observed, ["gemini-reported"])

    def test_openrouter_exact_model_and_no_identity_fallback(self):
        response = io.StringIO(json.dumps({"model": "reported-router-model", "choices": [{"finish_reason": "stop", "message": {"content": "answer"}}]}))
        opener = mock.Mock()
        opener.open.return_value = response
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "fake-direct", "OPENROUTER_API_KEY": "fake-router"}, clear=True), mock.patch.object(writer.urllib.request, "build_opener", return_value=opener):
            client = writer.Client("openrouter", "deepseek/deepseek-v3.2")
            client.generate("fictional", as_json=True)
        request = opener.open.call_args.args[0]
        self.assertEqual(json.loads(request.data)["model"], "deepseek/deepseek-v3.2")
        self.assertEqual(request.headers["Authorization"], "Bearer fake-router")
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "fake-direct"}, clear=True), self.assertRaisesRegex(ValueError, "OPENROUTER_API_KEY"):
            client.generate("fictional")

    def test_http_error_is_sanitized_and_never_retried(self):
        opener = mock.Mock()
        opener.open.side_effect = urllib.error.HTTPError("https://example.invalid", 401, "sensitive body", {}, io.BytesIO(b"secret payload"))
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "fake"}, clear=True), mock.patch.object(writer.urllib.request, "build_opener", return_value=opener):
            with self.assertRaisesRegex(ValueError, "HTTP 401") as error:
                writer.Client("gemini", writer.DEFAULT_MODEL).generate("private text")
        self.assertNotIn("sensitive", str(error.exception))
        self.assertEqual(opener.open.call_count, 1)

    def test_classification_fails_explicitly_and_preserves_literal_replacement(self):
        mark = {"id": "a", "p": 0, "quote": "old", "reason": "new"}
        for result in ("not JSON", '[]', '[{"id":"a","kind":"unknown","text":""}]'):
            client = mock.Mock()
            client.generate.return_value = result
            with self.subTest(result=result), self.assertRaises(ValueError):
                writer.classify([dict(mark)], [{"text": "old"}], client)
        client = mock.Mock()
        client.generate.return_value = '[{"id":"a","kind":"replace","text":" new "}]'
        writer.classify([mark], [{"text": "old"}], client)
        self.assertEqual(mark["replace"], " new ")

    def test_model_warnings_and_retired_model_rejection(self):
        with self.assertRaisesRegex(ValueError, "retired"):
            writer.Client("openrouter", "google/gemini-2.5-pro-preview-03-25")
        output = io.StringIO()
        with mock.patch("sys.stderr", output):
            writer.Client("openrouter", "google/gemini-3.7-flash")
        self.assertIn("3.8", output.getvalue())
        self.assertIn("cannot be guaranteed", output.getvalue())

    def test_invented_heading_is_rejected_without_writing_revision(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "meta.json").write_text('{"key":"fictional","title":"Fictional"}')
            (d / "v1.md").write_text("An event.\n")
            (d / "marks.json").write_text(json.dumps([{"id": "a", "v": 1, "p": 0, "start": 3, "end": 8, "quote": "event", "reason": ""}]))
            with mock.patch.object(writer.Client, "generate", return_value="# Invented title\n\nAn event."), self.assertRaisesRegex(ValueError, "heading"):
                writer.rewrite(d, d / "marks.json")
            self.assertFalse((d / "v2.md").exists())

    def test_writer_end_to_end_with_mock_generation(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "meta.json").write_text('{"key":"fictional","title":"Fictional"}')
            (d / "v1.md").write_text("# Event\n\nThe event is magnificent.\n\nDoors open at ten.\n\nBring a card.\n")
            marks = [{"id": "comment", "v": 1, "p": 1, "start": 13, "end": 24, "quote": "magnificent", "reason": "State a fact"},
                     {"id": "words", "v": 1, "p": 2, "start": 14, "end": 17, "quote": "ten", "reason": "nine"}]
            (d / "marks.json").write_text(json.dumps(marks))
            answers = [json.dumps([{"id":"comment","kind":"comment","text":""}, {"id":"words","kind":"replace","text":"nine"}]), "The event takes place at the library."]
            with mock.patch.object(writer.Client, "generate", side_effect=answers) as generate:
                writer.rewrite(d, d / "marks.json")
            self.assertEqual(generate.call_count, 2)
            self.assertIn("Doors open at nine.", generate.call_args.args[0])
            self.assertEqual((d / "v2.md").read_text(), "# Event\n\nThe event takes place at the library.\n\nDoors open at nine.\n\nBring a card.\n")
            result = json.loads((d / "rounds" / "r1-results.json").read_text())
            self.assertEqual(result["writer"]["provider"], "gemini")
            self.assertEqual(result["writer"]["requestedModel"], "gemini-2.5-pro")


if __name__ == "__main__":
    unittest.main()
