import unittest
from pathlib import Path

from crawler.extractor import count_words, extract_page

FIXTURE = Path(__file__).parent / "fixtures" / "sample.html"


class ExtractorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = extract_page(
            FIXTURE.read_bytes(),
            "https://example.com/article",
            content_type="text/html",
        )

    def test_basic_fields(self):
        self.assertEqual(self.page["title"], "测试页面 - 蛛网")
        self.assertEqual(self.page["lang"], "zh-CN")
        self.assertEqual(
            self.page["description"], "这是一个用于测试网页解析器的页面。"
        )
        self.assertEqual(self.page["favicon"], "https://example.com/favicon.ico")
        self.assertGreater(self.page["word_count"], 10)
        self.assertIn("这是一段中文正文", self.page["text"])

    def test_headings(self):
        headings = self.page["headings"]
        self.assertEqual(headings[0], {"level": 1, "text": "测试页面标题"})
        self.assertIn("数据表格", [item["text"] for item in headings])

    def test_links_are_absolute_and_deduplicated(self):
        links = self.page["links"]
        urls = [link["url"] for link in links]
        self.assertIn("https://example.com/relative?x=1", urls)
        self.assertEqual(urls.count("https://example.com/relative?x=1"), 1)
        self.assertIn("https://example.org/path", urls)
        self.assertEqual(sum(link["nofollow"] for link in links), 1)
        internal = {link["url"]: link["internal"] for link in links}
        self.assertTrue(internal["https://example.com/relative?x=1"])
        self.assertFalse(internal["https://example.org/path"])

    def test_images_and_lazy_loading(self):
        images = {image["url"]: image["alt"] for image in self.page["images"]}
        self.assertEqual(images["https://example.com/images/a.png"], "图片 A")
        self.assertEqual(images["https://example.com/images/b.png"], "延迟加载图片")

    def test_table_extraction(self):
        tables = self.page["tables"]
        self.assertEqual(len(tables), 1)
        self.assertEqual(tables[0]["caption"], "示例数据")
        self.assertEqual(tables[0]["rows"][1], ["苹果", "12"])

    def test_structured_data(self):
        self.assertEqual(self.page["structured_data"][0]["type"], "Article")
        self.assertEqual(self.page["structured_data"][0]["name"], "结构化数据标题")

    def test_scripts_removed_from_text(self):
        self.assertNotIn("@context", self.page["text"])
        self.assertNotIn("function", self.page["text"])

    def test_text_truncation(self):
        html = ("<html><body><p>" + "中文内容 " * 800 + "</p></body></html>")
        page = extract_page(
            html.encode("utf-8"),
            "https://example.com/long",
            max_text_chars=1_000,
        )
        self.assertTrue(page["text_truncated"])
        self.assertLessEqual(len(page["text"]), 1_001)
        self.assertTrue(page["text"].endswith("…"))

    def test_count_words(self):
        self.assertEqual(count_words("你好 hello world 123"), 5)


if __name__ == "__main__":
    unittest.main()
