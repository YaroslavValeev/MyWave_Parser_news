import unittest

from utils.card_preview_text import (
    normalize_raw_feed_card_fields,
    normalize_publication_text,
    public_text_quality_issues,
    strip_markdown_to_plain,
    to_card_preview_text,
)


class TestCardPreviewText(unittest.TestCase):
    def test_strip_bold_and_link(self):
        s = "Intro [**bold**](https://x.com/y) end"
        self.assertNotIn("**", strip_markdown_to_plain(s))
        self.assertNotIn("http", strip_markdown_to_plain(s).lower())

    def test_to_card_preview_removes_url(self):
        t = "See https://example.com/very/long/path for more"
        out = to_card_preview_text(t, max_len=260)
        self.assertNotIn("http", out)
        self.assertIn("See", out)

    def test_normalize_fills_excerpt_from_final_posts(self):
        row = {
            "final_posts": "# Title\n\nPara with **bold** and https://a.com/x.",
            "summary": "",
            "excerpt": "",
            "lead": "",
            "meta_description": "",
            "og_description": "",
        }
        normalize_raw_feed_card_fields(row)
        self.assertNotIn("**", row["excerpt"])
        self.assertNotIn("http", row["excerpt"])
        self.assertTrue(row["summary"])
        self.assertNotIn("**", row["summary"])
        self.assertEqual(row["excerpt"], row["lead"])
        self.assertNotIn("**", row["lead"])

    def test_normalize_does_not_fill_from_raw_when_final_empty(self):
        row = {
            "final_posts": "",
            "raw_content": "x" * 5000,
            "summary": "",
            "excerpt": "",
            "lead": "",
        }
        normalize_raw_feed_card_fields(row)
        self.assertEqual(row.get("excerpt"), "")
        self.assertEqual(row.get("summary"), "")
        self.assertEqual(row.get("lead"), "")

    def test_final_posts_are_public_clean_text(self):
        md = "## H\n\n[link](https://z.com) **x**"
        row = {"final_posts": md, "excerpt": ""}
        normalize_raw_feed_card_fields(row)
        self.assertEqual(row["final_posts"], "H\n\nlink x")

    def test_publication_text_removes_nested_markdown_artifacts(self):
        out = normalize_publication_text("**__ВЕБИНАР Профобразование__**\n\nassistant: draft")
        self.assertEqual(out, "ВЕБИНАР Профобразование")

    def test_quality_gate_detects_dirty_text_and_short_excerpt(self):
        issues = public_text_quality_issues(
            title="**__ВЕБИНАР__**",
            excerpt="short",
            body="system: do this\n### Draft\n" + ("Основной текст публикации. " * 10),
        )
        self.assertIn("markdown_bold", issues)
        self.assertIn("markdown_underscore", issues)
        self.assertIn("ai_role", issues)
        self.assertIn("markdown_heading", issues)
        self.assertIn("excerpt_too_short", issues)


if __name__ == "__main__":
    unittest.main()
