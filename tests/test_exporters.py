import csv
import json
import tempfile
import unittest
from pathlib import Path

from crawler.exporters import save_csv, save_json


class ExporterTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.dir = Path(self.tempdir.name)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_json_is_utf8_and_readable(self):
        path = self.dir / "result.json"
        save_json({"title": "中文标题", "pages": []}, path)
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["title"], "中文标题")

    def test_csv_has_utf8_bom_and_page_columns(self):
        path = self.dir / "pages.csv"
        pages = [
            {
                "url": "https://example.com/",
                "title": "标题",
                "word_count": 12,
                "links": [{"url": "a"}],
                "images": [],
                "tables": [],
                "text": "正文",
            }
        ]
        save_csv(pages, path)
        raw = path.read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
        with path.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(rows[0]["title"], "标题")
        self.assertEqual(rows[0]["link_count"], "1")

    def test_csv_blocks_formula_injection(self):
        path = self.dir / "pages.csv"
        save_csv(
            [
                {
                    "url": "https://example.com/",
                    "title": "=1+1",
                    "description": "@SUM(A1:A2)",
                    "text": "+cmd|' /C calc'!A0",
                }
            ],
            path,
        )
        with path.open(encoding="utf-8-sig", newline="") as handle:
            row = next(csv.DictReader(handle))
        self.assertEqual(row["title"], "'=1+1")
        self.assertEqual(row["description"], "'@SUM(A1:A2)")
        self.assertEqual(row["text"], "'+cmd|' /C calc'!A0")


if __name__ == "__main__":
    unittest.main()
