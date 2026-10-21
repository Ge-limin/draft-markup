"""Offline checks using fictional drafts; no provider credentials or network."""
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
from html.parser import HTMLParser
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import markup


def page_data(article):
    page = (article / "page.html").read_text()
    match = re.search(r"const DATA = (.+);\n", page)
    return page, json.loads(match.group(1))


class MarkupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.article = Path(self.temp.name) / "article"
        self.article.mkdir()

    def write_draft(self, title, body):
        (self.article / "meta.json").write_text(json.dumps({"key": "fictional-test", "title": title}))
        (self.article / "brief.md").write_text("Fictional test notice. Use only the supplied facts.")
        (self.article / "v1.md").write_text(body)

    def cli(self, *args, success=True):
        env = os.environ.copy()
        env.pop("GEMINI_API_KEY", None)
        env.pop("OPENROUTER_API_KEY", None)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        result = subprocess.run([sys.executable, str(ROOT / "markup.py"), *map(str, args)],
                                env=env, capture_output=True, text=True, timeout=15)
        if success:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0)
        return result

    def marks_file(self, marks, **overrides):
        envelope = {"schema": "draft-markup/marks-v1", "key": "fictional-test", "version": 1,
                    "sourceHash": markup.source_hash(self.article, 1), "offsetEncoding": "utf16", "marks": marks}
        envelope.update(overrides)
        path = self.article / "marks.json"
        path.write_text(json.dumps(envelope))
        return path

    def test_demo_cli_build_prepare_apply_compare_without_credentials(self):
        for name in ("meta.json", "brief.md", "v1.md", "marks.json"):
            shutil.copyfile(ROOT / "examples" / "demo" / name, self.article / name)
        original = (self.article / "v1.md").read_text()
        self.cli("build", self.article)
        _, first = page_data(self.article)
        self.assertEqual(first["version"], 1)
        self.assertEqual(first["sourceHash"], hashlib.sha256(original.encode()).hexdigest())
        self.cli("prepare", self.article, self.article / "marks.json")
        self.cli("apply", self.article, self.article / "task.json", self.article / "task.results.json")
        self.assertEqual((self.article / "v1.md").read_text(), original)
        self.assertEqual((self.article / "v2.md").read_text(),
                         "# A fictional library notice\n\nThe library opens at nine. \n\nPlease return books at the front desk.\n")
        self.cli("build", self.article)
        _, second = page_data(self.article)
        self.assertEqual(second["version"], 2)
        self.assertIn("It is an amazing place.", second["prevBlocks"][1]["text"])
        self.assertNotIn("amazing", second["blocks"][1]["text"])
        self.assertEqual(second["changes"][0]["oldP"], [1])
        self.assertEqual(second["changes"][0]["marks"][0]["replace"], "")
        self.assertTrue((self.article / "rounds" / "r1-task.json").exists())
        self.assertIn("It is an amazing place.", (self.article / "划线记录.md").read_text())

    def test_html_title_and_embedded_json_do_not_close_tags(self):
        title = '</title><script>alert("fiction")</script> & notice'
        body = "Fictional text containing </script><script>alert('demo')</script>.\n"
        self.write_draft(title, body)
        self.cli("build", self.article)
        page, data = page_data(self.article)
        self.assertTrue(page.startswith("<title>&lt;/title&gt;&lt;script&gt;"))
        self.assertEqual(page.count("</script>"), 1)
        parser, tags = HTMLParser(), []
        parser.handle_starttag = lambda tag, attrs: tags.append(tag)
        parser.feed(page)
        self.assertEqual(tags.count("script"), 1)
        self.assertEqual(data["title"], title)
        self.assertEqual(data["blocks"][0]["text"], body.strip())

    def test_utf16_emoji_offsets_and_surrogate_boundary(self):
        self.write_draft("Emoji", "😀 hello world\n")
        mark = {"id": "a", "v": 1, "p": 0, "start": 3, "end": 8, "quote": "hello", "replace": "你好"}
        task = markup.make_task(self.article, markup.load_marks(self.marks_file([mark]), self.article))
        self.assertEqual(task["marks"][0]["start"], 2)
        markup.apply(self.article, task, markup.result_template(task))
        self.assertEqual((self.article / "v2.md").read_text(), "😀 你好 world\n")
        with self.assertRaisesRegex(ValueError, "UTF-16"):
            markup.utf16_offset("😀 hello", 1)

    def test_reject_stale_envelopes_ranges_quotes_and_overlap(self):
        self.write_draft("Test", "hello world\n")
        good = {"id": "a", "v": 1, "p": 0, "start": 0, "end": 5, "quote": "hello", "replace": "hi"}
        for change in ({"version": 2}, {"sourceHash": "wrong"}, {"key": "another"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                markup.load_marks(self.marks_file([good], **change), self.article)
        for change in ({"v": 2}, {"p": 9}, {"quote": "wrong"}, {"end": 99}, {"start": -1}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                markup.load_marks(self.marks_file([{**good, **change}]), self.article)
        overlap = {**good, "id": "b", "start": 2, "quote": "llo"}
        with self.assertRaisesRegex(ValueError, "overlapping"):
            markup.load_marks(self.marks_file([good, overlap]), self.article)
        with self.assertRaisesRegex(ValueError, "unique"):
            markup.load_marks(self.marks_file([good, good]), self.article)

    def test_manual_comment_rewrite_preserves_author_text_and_untouched_paragraphs(self):
        self.write_draft("Test", "# Title\n\nEvent wonderful at ten.\n\nBring a card.\n")
        marks = [{"id": "a", "v": 1, "p": 1, "start": 6, "end": 15, "quote": "wonderful", "reason": "State a fact"},
                 {"id": "b", "v": 1, "p": 1, "start": 19, "end": 22, "quote": "ten", "replace": "nine"}]
        self.cli("prepare", self.article, self.marks_file(marks))
        task_path, results_path = self.article / "task.json", self.article / "task.results.json"
        task = json.loads(task_path.read_text())
        results = json.loads(results_path.read_text())
        self.assertIn("nine", task["article"])
        self.assertEqual(task["groups"][0]["preserve"], ["nine"])
        for bad in ({"groups": []}, {"sourceHash": "wrong"},
                    {"groups": [{"id": "g0", "text": "Event at ten."}]},
                    {"groups": [{"id": "g0", "text": "nine"}, {"id": "g0", "text": "nine"}]},
                    {"groups": [{"id": "g0", "text": "nine"}, {"id": "g1", "text": "nine"}]}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                markup.apply(self.article, task, {**results, **bad})
        results["groups"][0]["text"] = "Event starts at nine."
        results_path.write_text(json.dumps(results))
        self.cli("apply", self.article, task_path, results_path)
        self.assertEqual((self.article / "v2.md").read_text(), "# Title\n\nEvent starts at nine.\n\nBring a card.\n")
        self.cli("apply", self.article, task_path, results_path, success=False)

    def test_task_tampering_and_changed_source_rejected(self):
        self.write_draft("Test", "hello world\n")
        marks = [{"id": "a", "v": 1, "p": 0, "start": 0, "end": 5, "quote": "hello", "replace": "hi"}]
        task = markup.make_task(self.article, marks)
        edited = copy.deepcopy(task)
        edited["article"] = "unrelated"
        with self.assertRaises(ValueError):
            markup.apply(self.article, edited, markup.result_template(edited))
        (self.article / "v1.md").write_text("hello changed\n")
        with self.assertRaises(ValueError):
            markup.apply(self.article, task, markup.result_template(task))
        self.assertFalse((self.article / "v2.md").exists())

    def test_reported_write_failure_rolls_back_records_and_version(self):
        self.write_draft("Test", "hello world\n")
        (self.article / "划线记录.md").write_text("previous log\n")
        marks = [{"id": "a", "v": 1, "p": 0, "start": 0, "end": 5, "quote": "hello", "replace": "hi"}]
        task = markup.make_task(self.article, marks)
        real_replace = os.replace
        def fail_version(source, target):
            if Path(target).name == "v2.md":
                raise OSError("simulated full disk")
            return real_replace(source, target)
        with mock.patch.object(markup.os, "replace", side_effect=fail_version), self.assertRaises(OSError):
            markup.apply(self.article, task, markup.result_template(task))
        self.assertFalse((self.article / "v2.md").exists())
        self.assertEqual((self.article / "划线记录.md").read_text(), "previous log\n")
        self.assertEqual(list((self.article / "rounds").iterdir()), [])
        self.assertEqual(list(self.article.glob(".markup-*")), [])

    def test_legacy_artifact_document_and_explicit_delete(self):
        self.write_draft("Test", "hello world\n")
        path = self.article / "legacy.json"
        path.write_text(json.dumps({"id": "legacy", "data": {"v": 1, "p": 0, "start": 0, "end": 5, "quote": "hello", "reason": "删"}}))
        marks = markup.load_marks(path, self.article)
        self.assertEqual(marks[0]["replace"], "")
        self.assertEqual(marks[0]["id"], "legacy")

    def test_verified_links_only_touch_marked_paragraphs_and_utf16_spans(self):
        self.write_draft("Links", "😀 Visit library now.\n\nVisit library tomorrow.\n")
        (self.article / "links.json").write_text(json.dumps([{"anchors": ["library"], "url": "https://example.org/library"}]))
        marks = [{"id": "a", "v": 1, "p": 0, "start": 16, "end": 19, "quote": "now", "replace": "today"}]
        task = markup.make_task(self.article, marks)
        markup.apply(self.article, task, markup.result_template(task))
        self.assertEqual((self.article / "v2.md").read_text(), "😀 Visit [library](https://example.org/library) today.\n\nVisit library tomorrow.\n")
        self.cli("build", self.article)
        _, data = page_data(self.article)
        self.assertEqual(data["blocks"][0]["links"], [[9, 16, "https://example.org/library"]])

    def test_core_has_no_provider_import_or_network(self):
        code = (ROOT / "markup.py").read_text()
        self.assertNotIn("urllib", code)
        self.assertNotIn("import writer", code)
        self.assertNotIn("API_KEY", code)
        result = self.cli("rewrite", self.article, "unused", success=False)
        self.assertIn("writer.py", result.stderr)


if __name__ == "__main__":
    unittest.main()
