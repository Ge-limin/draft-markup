"""Offline checks using fictional drafts; run with unittest discovery."""

import contextlib
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
SPEC = importlib.util.spec_from_file_location("markup", ROOT / "markup.py")
markup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(markup)


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

    def cli(self, *args):
        env = os.environ.copy()
        env.pop("GEMINI_API_KEY", None)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        result = subprocess.run(
            [sys.executable, str(ROOT / "markup.py"), *map(str, args)],
            env=env, capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def test_demo_cli_build_delete_and_compare_without_credentials(self):
        for name in ("meta.json", "brief.md", "v1.md", "marks.json"):
            shutil.copyfile(ROOT / "examples" / "demo" / name, self.article / name)
        original = (self.article / "v1.md").read_text()
        self.cli("build", self.article)
        _, first = page_data(self.article)
        self.assertEqual(first["version"], 1)
        self.assertEqual(first["prevBlocks"], [])

        self.cli("rewrite", self.article, self.article / "marks.json")
        self.assertEqual((self.article / "v1.md").read_text(), original)
        self.assertEqual(
            (self.article / "v2.md").read_text(),
            "# A fictional library notice\n\nThe library opens at nine. \n\nPlease return books at the front desk.\n",
        )
        self.cli("build", self.article)
        _, second = page_data(self.article)
        self.assertEqual(second["version"], 2)
        self.assertIn("It is an amazing place.", second["prevBlocks"][1]["text"])
        self.assertNotIn("amazing", second["blocks"][1]["text"])
        self.assertEqual(second["changes"][0]["oldP"], [1])
        self.assertEqual(second["changes"][0]["marks"][0]["replace"], "")
        self.assertTrue((self.article / "rounds" / "r1-marks.json").exists())
        self.assertIn("It is an amazing place.", (self.article / "划线记录.md").read_text())

    def test_html_title_and_embedded_json_do_not_close_tags(self):
        title = '</title><script>alert("fiction")</script> & notice'
        body = "Fictional text containing </script><script>alert('demo')</script>.\n"
        self.write_draft(title, body)
        self.cli("build", self.article)
        page, data = page_data(self.article)
        self.assertTrue(page.startswith("<title>&lt;/title&gt;&lt;script&gt;"))
        self.assertEqual(page.count("</script>"), 1)
        parser = HTMLParser()
        tags = []
        parser.handle_starttag = lambda tag, attrs: tags.append(tag)
        parser.feed(page)
        self.assertEqual(tags.count("script"), 1)
        self.assertEqual(data["title"], title)
        self.assertEqual(data["blocks"][0]["text"], body.strip())

    def test_classified_comment_rewrites_only_marked_paragraph(self):
        self.write_draft(
            "Fictional event",
            "# Fictional event\n\nThe event is magnificent.\n\nDoors open at ten.\n\nBring a library card.\n",
        )
        marks = [
            {"id": "comment", "v": 1, "p": 1, "start": 13, "end": 24,
             "quote": "magnificent", "reason": "Use a factual description."},
            {"id": "words", "v": 1, "p": 2, "start": 14, "end": 17,
             "quote": "ten", "reason": "nine"},
        ]
        marks_file = self.article / "marks.json"
        marks_file.write_text(json.dumps(marks))

        def fake_gemini(model, prompt, as_json=False):
            if as_json:
                return json.dumps([
                    {"id": "comment", "kind": "comment", "text": ""},
                    {"id": "words", "kind": "replace", "text": "nine"},
                ])
            self.assertIn("Doors open at nine.", prompt)
            self.assertIn("Use a factual description.", prompt)
            return "The event takes place at the library."

        with mock.patch.object(markup, "gemini", side_effect=fake_gemini) as api, contextlib.redirect_stdout(io.StringIO()):
            markup.rewrite(self.article, marks_file, "offline-test-model")
        self.assertEqual(api.call_count, 2)
        self.assertEqual(
            (self.article / "v2.md").read_text(),
            "# Fictional event\n\nThe event takes place at the library.\n\nDoors open at nine.\n\nBring a library card.\n",
        )
        changes = json.loads((self.article / "rounds" / "r1-changes.json").read_text())
        self.assertEqual([c["oldP"] for c in changes["changes"]], [[1], [2]])
        self.assertEqual(changes["changes"][1]["marks"][0]["replace"], "nine")


if __name__ == "__main__":
    unittest.main()
