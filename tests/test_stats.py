"""Tests for scripts/stats.py. Run with: python -m unittest discover -s tests"""

import sys
import unittest
import xml.dom.minidom
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import stats  # noqa: E402


def calendar(start: date, counts: list[int]) -> dict:
    days = [{"date": (start + timedelta(days=i)).isoformat(), "contributionCount": c} for i, c in enumerate(counts)]
    return {"totalCommitContributions": sum(counts), "contributionCalendar": {"totalContributions": sum(counts), "weeks": [{"contributionDays": days}]}}


RAW = {
    "first_year": 2023,
    "profile": {
        "createdAt": "2023-02-01T00:00:00Z",
        "followers": {"totalCount": 12},
        "pullRequests": {"totalCount": 40},
        "issues": {"totalCount": 17},
    },
    "repos": [
        {"name": "a", "isPrivate": False, "stargazerCount": 5, "languages": {"edges": [
            {"size": 600, "node": {"name": "Python", "color": "#3572A5"}},
            {"size": 200, "node": {"name": "Shell", "color": None}},
        ]}},
        {"name": "b", "isPrivate": False, "stargazerCount": 2, "languages": {"edges": [
            {"size": 200, "node": {"name": "Python", "color": "#3572A5"}},
            {"size": 1000, "node": {"name": "TypeScript", "color": "#3178c6"}},
        ]}},
    ],
    "years": [calendar(date(2026, 10, 1), [1, 1, 0, 2, 3, 4, 0, 1, 1, 0, 5, 1, 1])],
}


class StreakTests(unittest.TestCase):
    def days(self, counts: list[int]) -> dict[date, int]:
        return stats.contribution_days([calendar(date(2026, 10, 1), counts)])

    def test_longest_and_current(self) -> None:
        current, longest = stats.streaks(self.days([1, 1, 0, 2, 3, 4, 0, 1, 1, 0, 5, 1, 1]), date(2026, 10, 13))
        self.assertEqual((longest.length, longest.start, longest.end), (3, date(2026, 10, 4), date(2026, 10, 6)))
        self.assertEqual((current.length, current.start, current.end), (3, date(2026, 10, 11), date(2026, 10, 13)))

    def test_nothing_yet_today_keeps_the_streak(self) -> None:
        current, _ = stats.streaks(self.days([1, 1, 1, 0]), date(2026, 10, 4))
        self.assertEqual((current.length, current.end), (3, date(2026, 10, 3)))

    def test_missed_yesterday_breaks_it(self) -> None:
        current, longest = stats.streaks(self.days([1, 1, 0, 0]), date(2026, 10, 4))
        self.assertEqual(current.length, 0)
        self.assertEqual(longest.length, 2)

    def test_future_days_are_ignored(self) -> None:
        current, longest = stats.streaks(self.days([1, 1, 1, 1, 1]), date(2026, 10, 2))
        self.assertEqual((current.length, longest.length), (2, 2))

    def test_gap_in_the_data_breaks_a_run(self) -> None:
        days = {date(2026, 1, 1): 1, date(2026, 1, 3): 1}
        _, longest = stats.streaks(days, date(2026, 1, 3))
        self.assertEqual(longest.length, 1)

    def test_no_contributions(self) -> None:
        current, longest = stats.streaks({}, date(2026, 1, 1))
        self.assertEqual((current.length, longest.length), (0, 0))
        self.assertEqual(stats.date_range(current), "No streak yet")


class SummaryTests(unittest.TestCase):
    def test_numbers(self) -> None:
        s = stats.summarize(RAW, date(2026, 10, 13))
        self.assertEqual((s.stars, s.pull_requests, s.issues, s.repos, s.followers), (7, 40, 17, 2, 12))
        self.assertEqual(s.commits, 20)
        self.assertEqual(s.total_contributions, 20)
        self.assertEqual(s.contributions_this_year, 20)

    def test_this_year_uses_the_latest_year_only(self) -> None:
        raw = {**RAW, "years": [calendar(date(2025, 3, 1), [4, 4]), calendar(date(2026, 3, 1), [1, 2])]}
        s = stats.summarize(raw, date(2026, 3, 2))
        self.assertEqual((s.total_contributions, s.contributions_this_year), (11, 3))

    def test_languages_ranked_by_bytes_with_percentages(self) -> None:
        langs = stats.top_languages(RAW["repos"])
        self.assertEqual([lang.name for lang in langs], ["TypeScript", "Python", "Shell"])
        self.assertEqual([lang.percent for lang in langs], [50.0, 40.0, 10.0])
        self.assertEqual(langs[2].color, stats.MUTED)  # no color from GitHub
        self.assertEqual(len(stats.top_languages(RAW["repos"], limit=1)), 1)

    def test_number_format(self) -> None:
        self.assertEqual(stats.number(999), "999")
        self.assertEqual(stats.number(1234), "1,234")
        self.assertEqual(stats.number(12_345), "12.3k")

    def test_date_range(self) -> None:
        self.assertEqual(stats.date_range(stats.Streak(1, date(2026, 1, 2), date(2026, 1, 2))), "Jan 2, 2026")
        self.assertEqual(
            stats.date_range(stats.Streak(3, date(2025, 12, 31), date(2026, 1, 2))),
            "Dec 31, 2025 – Jan 2, 2026",
        )


class RenderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.s = stats.summarize(RAW, date(2026, 10, 13))

    def assert_valid_svg(self, svg: str) -> None:
        doc = xml.dom.minidom.parseString(svg.encode())  # raises if the XML is malformed
        self.assertEqual(doc.documentElement.tagName, "svg")

    def test_cards_are_valid_svg_with_the_numbers(self) -> None:
        card = stats.render_stats(self.s, "Zeiad")
        self.assert_valid_svg(card)
        self.assertIn(">40<", card)
        self.assert_valid_svg(stats.render_languages(self.s.languages))
        streak = stats.render_streak(self.s, date(2026, 10, 13))
        self.assert_valid_svg(streak)
        self.assertIn("Oct 11, 2026 – Oct 13, 2026", streak)

    def test_text_is_escaped(self) -> None:
        card = stats.render_stats(self.s, "<script>&")
        self.assert_valid_svg(card)
        self.assertNotIn("<script>", card)

    def test_empty_languages(self) -> None:
        self.assertIn("No language data yet", stats.render_languages([]))


if __name__ == "__main__":
    unittest.main()


class CheckImagesTests(unittest.TestCase):
    def test_finds_all_image_syntaxes_without_mangling_query_strings(self) -> None:
        import check_images

        text = (
            '![a](https://x.io/a.svg) <img src="https://x.io/b?height=1&section=header&center=true"/>\n'
            '<source srcset="https://x.io/c.svg"/> <img src="assets/d.svg"/> <img src="https://x.io/e?a=1&amp;b=2"/>'
        )
        self.assertEqual(
            check_images.image_urls(text),
            [
                "https://x.io/a.svg",
                "https://x.io/b?height=1&section=header&center=true",
                "assets/d.svg",
                "https://x.io/e?a=1&b=2",
                "https://x.io/c.svg",
            ],
        )
