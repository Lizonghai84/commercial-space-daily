import datetime as dt
import unittest
from zoneinfo import ZoneInfo

from space_daily import Item, SourceStatus, classify, clean_text, deduplicate, is_relevant, normalize_title, render_report


NOW = dt.datetime(2026, 6, 5, 0, 0, tzinfo=dt.timezone.utc)


def make_item(title: str, source: str = "SpaceNews", weight: int = 100) -> Item:
    return Item(
        title=title,
        url=f"https://example.com/{source}/{title}",
        source=source,
        published_at=NOW - dt.timedelta(hours=1),
        summary="Commercial satellite launch update.",
        category="行业动态",
        source_weight=weight,
    )


class SpaceDailyTests(unittest.TestCase):
    def test_normalize_title_removes_noise(self):
        self.assertEqual(normalize_title("The Launch of a New Satellite"), "launch new satellite")

    def test_clean_text_removes_escaped_html(self):
        self.assertEqual(clean_text("&lt;div&gt;Satellite result&lt;/div&gt;"), "Satellite result")

    def test_classify_technology(self):
        self.assertEqual(classify("Reusable rocket engine test", ""), "最新技术")

    def test_equity_research_note_is_not_academic_research(self):
        self.assertEqual(classify("Satellite company update", "An equity research note"), "行业动态")

    def test_pure_science_spacecraft_story_is_not_commercial(self):
        self.assertFalse(is_relevant("NASA ends Mars mission", "The spacecraft is no longer responding."))

    def test_deduplicate_keeps_higher_weight_and_tracks_source(self):
        items = [
            make_item("SpaceX launches Starlink Group 10-43", "SpaceNews", 100),
            make_item("SpaceX launches Starlink group 10 43", "Other", 70),
        ]
        result = deduplicate(items, NOW)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].source, "SpaceNews")
        self.assertEqual(result[0].related_sources, ["Other"])

    def test_render_report_contains_all_sections(self):
        item = make_item("Commercial satellite launch")
        item.score = 120
        report = render_report(
            [item],
            [SourceStatus("SpaceNews", True, 1)],
            dt.date(2026, 6, 5),
            NOW.astimezone(ZoneInfo("Asia/Shanghai")),
            36,
            8,
        )
        for category in ("行业动态", "最新技术", "研究成果", "最新发射"):
            self.assertIn(f"## {category}", report)
        self.assertIn("# 商业航天日报 | 2026-06-05", report)


if __name__ == "__main__":
    unittest.main()
