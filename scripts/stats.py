"""Generate the profile's GitHub stats cards as SVG files.

Runs in GitHub Actions (see .github/workflows/profile.yml). It asks GitHub's GraphQL
API for the real numbers and draws three cards, so the README doesn't depend on a
shared third-party server that gets rate-limited.

    GH_TOKEN=... python scripts/stats.py zeiado dist/

Standard library only. With the default Actions token, private repositories aren't
visible; set a STATS_TOKEN secret (classic token with `repo` and `read:user`) to
include them.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from html import escape
from pathlib import Path
from typing import Any

API = "https://api.github.com/graphql"

# Theme: matches the rest of the profile README.
BG = "#0d1117"
BORDER = "#30363d"
ACCENT = "#a78bfa"
TEXT = "#e6edf3"
MUTED = "#8b949e"
FIRE = "#ff6b6b"
FONT = "'Segoe UI', Ubuntu, 'Helvetica Neue', Sans-Serif"

PROFILE_QUERY = """
query($login: String!) {
  user(login: $login) {
    createdAt
    followers { totalCount }
    pullRequests { totalCount }
    issues { totalCount }
  }
}
"""

REPOS_QUERY = """
query($login: String!, $after: String) {
  user(login: $login) {
    repositories(ownerAffiliations: OWNER, isFork: false, first: 100, after: $after) {
      totalCount
      pageInfo { hasNextPage endCursor }
      nodes {
        name
        isPrivate
        stargazerCount
        languages(first: 20, orderBy: {field: SIZE, direction: DESC}) {
          edges { size node { name color } }
        }
      }
    }
  }
}
"""

YEAR_QUERY = """
query($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      totalCommitContributions
      contributionCalendar {
        totalContributions
        weeks { contributionDays { date contributionCount } }
      }
    }
  }
}
"""


# --- Data model ---------------------------------------------------------------


@dataclass
class Language:
    name: str
    color: str
    size: int
    percent: float = 0.0


@dataclass
class Streak:
    length: int = 0
    start: date | None = None
    end: date | None = None


@dataclass
class Stats:
    stars: int
    commits: int
    pull_requests: int
    issues: int
    contributions_this_year: int
    repos: int
    followers: int
    total_contributions: int
    first_year: int
    current_streak: Streak
    longest_streak: Streak
    languages: list[Language] = field(default_factory=list)


# --- Fetching -----------------------------------------------------------------


def graphql(query: str, variables: dict[str, Any], token: str) -> dict[str, Any]:
    request = urllib.request.Request(
        API,
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={"Authorization": f"bearer {token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 (fixed https URL)
        body: dict[str, Any] = json.load(response)
    if body.get("errors"):
        raise RuntimeError(f"GitHub API error: {body['errors']}")
    data: dict[str, Any] = body["data"]
    return data


def fetch(login: str, token: str, now: datetime) -> dict[str, Any]:
    """Everything the cards need, as raw API data."""
    profile = graphql(PROFILE_QUERY, {"login": login}, token)["user"]

    repos: list[dict[str, Any]] = []
    after = None
    while True:
        page = graphql(REPOS_QUERY, {"login": login, "after": after}, token)["user"]["repositories"]
        repos += page["nodes"]
        if not page["pageInfo"]["hasNextPage"]:
            break
        after = page["pageInfo"]["endCursor"]

    # The contributions API covers at most one year per request.
    first_year = datetime.fromisoformat(profile["createdAt"].replace("Z", "+00:00")).year
    years = []
    for year in range(first_year, now.year + 1):
        start = datetime(year, 1, 1, tzinfo=UTC)
        end = min(datetime(year, 12, 31, 23, 59, 59, tzinfo=UTC), now)
        collection = graphql(
            YEAR_QUERY,
            {"login": login, "from": start.isoformat(), "to": end.isoformat()},
            token,
        )["user"]["contributionsCollection"]
        years.append(collection)

    return {"profile": profile, "repos": repos, "years": years, "first_year": first_year}


# --- Pure calculations --------------------------------------------------------


def contribution_days(years: list[dict[str, Any]]) -> dict[date, int]:
    days: dict[date, int] = {}
    for year in years:
        for week in year["contributionCalendar"]["weeks"]:
            for day in week["contributionDays"]:
                days[date.fromisoformat(day["date"])] = day["contributionCount"]
    return days


def streaks(days: dict[date, int], today: date) -> tuple[Streak, Streak]:
    """Current and longest run of consecutive days with at least one contribution.

    Today doesn't break the current streak until it's over: if there's nothing yet
    today, the streak is counted up to yesterday.
    """
    longest = Streak()
    run = Streak()
    for day in sorted(d for d in days if d <= today):
        if days[day] > 0:
            if run.end is not None and day - run.end == timedelta(days=1):
                run = Streak(run.length + 1, run.start, day)
            else:
                run = Streak(1, day, day)
            if run.length > longest.length:
                longest = run
        else:
            run = Streak()

    current = Streak()
    cursor = today if days.get(today, 0) > 0 else today - timedelta(days=1)
    while days.get(cursor, 0) > 0:
        current = Streak(current.length + 1, cursor, current.end or cursor)
        cursor -= timedelta(days=1)
    return current, longest


def top_languages(repos: list[dict[str, Any]], limit: int = 8) -> list[Language]:
    totals: dict[str, Language] = {}
    for repo in repos:
        for edge in repo["languages"]["edges"]:
            name = edge["node"]["name"]
            lang = totals.setdefault(name, Language(name, edge["node"]["color"] or MUTED, 0))
            lang.size += edge["size"]
    ranked = sorted(totals.values(), key=lambda lang: lang.size, reverse=True)[:limit]
    total = sum(lang.size for lang in ranked)
    for lang in ranked:
        lang.percent = round(100 * lang.size / total, 1) if total else 0.0
    return ranked


def summarize(raw: dict[str, Any], today: date) -> Stats:
    profile, repos, years = raw["profile"], raw["repos"], raw["years"]
    current, longest = streaks(contribution_days(years), today)
    return Stats(
        stars=sum(r["stargazerCount"] for r in repos),
        commits=sum(y["totalCommitContributions"] for y in years),
        pull_requests=profile["pullRequests"]["totalCount"],
        issues=profile["issues"]["totalCount"],
        # The last entry is the current year (fetch() goes oldest to newest).
        contributions_this_year=years[-1]["contributionCalendar"]["totalContributions"] if years else 0,
        repos=len(repos),
        followers=profile["followers"]["totalCount"],
        total_contributions=sum(y["contributionCalendar"]["totalContributions"] for y in years),
        first_year=raw["first_year"],
        current_streak=current,
        longest_streak=longest,
        languages=top_languages(repos),
    )


# --- Rendering ----------------------------------------------------------------


def number(n: int) -> str:
    return f"{n / 1000:.1f}k" if n >= 10_000 else f"{n:,}"


def date_range(streak: Streak) -> str:
    if streak.start is None or streak.end is None:
        return "No streak yet"

    def fmt(d: date) -> str:
        return f"{d:%b} {d.day}, {d.year}"

    return fmt(streak.start) if streak.start == streak.end else f"{fmt(streak.start)} – {fmt(streak.end)}"


def card(width: int, height: int, title: str, body: str, label: str) -> str:
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="{escape(label)}">
  <title>{escape(label)}</title>
  <style>
    text {{ font-family: {FONT}; }}
    .title {{ fill: {ACCENT}; font-size: 18px; font-weight: 600; }}
    .label {{ fill: {TEXT}; font-size: 14px; }}
    .value {{ fill: {TEXT}; font-size: 14px; font-weight: 700; }}
    .muted {{ fill: {MUTED}; font-size: 12px; }}
    /* Visible by default; the animation only adds a fade-in where it runs. */
    .fade {{ animation: fade 0.6s ease-out both; }}
    @keyframes fade {{ from {{ opacity: 0; transform: translateX(-6px); }} to {{ opacity: 1; transform: none; }} }}
    @media (prefers-reduced-motion: reduce) {{ .fade {{ animation: none; }} }}
  </style>
  <rect x="0.5" y="0.5" width="{width - 1}" height="{height - 1}" rx="10" fill="{BG}" stroke="{BORDER}"/>
  <text x="25" y="35" class="title">{escape(title)}</text>
{body}
</svg>
"""


def render_stats(stats: Stats, name: str) -> str:
    rows = [
        ("Total stars earned", stats.stars),
        ("Commits (all time)", stats.commits),
        ("Pull requests", stats.pull_requests),
        ("Issues", stats.issues),
        ("Contributions this year", stats.contributions_this_year),
        ("Repositories", stats.repos),
    ]
    body = []
    for i, (label, value) in enumerate(rows):
        y = 68 + i * 24
        body.append(
            f'  <g class="fade" style="animation-delay:{150 + i * 120}ms">'
            f'<circle cx="31" cy="{y - 5}" r="4" fill="{ACCENT}"/>'
            f'<text x="45" y="{y}" class="label">{escape(label)}:</text>'
            f'<text x="300" y="{y}" class="value" text-anchor="end">{number(value)}</text></g>'
        )
    # Big number on the right: total contributions, all time.
    body.append(
        f'  <g class="fade" style="animation-delay:200ms">'
        f'<circle cx="390" cy="112" r="52" fill="none" stroke="{ACCENT}" stroke-opacity="0.25" stroke-width="7"/>'
        f'<circle cx="390" cy="112" r="52" fill="none" stroke="{ACCENT}" stroke-width="7" stroke-linecap="round" '
        f'stroke-dasharray="327" stroke-dashoffset="0" transform="rotate(-90 390 112)">'
        f'<animate attributeName="stroke-dashoffset" from="327" to="0" dur="1.2s" fill="freeze"/></circle>'
        f'<text x="390" y="112" class="value" text-anchor="middle" style="font-size:22px">{number(stats.total_contributions)}</text>'
        f'<text x="390" y="132" class="muted" text-anchor="middle">contributions</text></g>'
    )
    title = f"{name}'s GitHub Stats"
    return card(467, 215, title, "\n".join(body), f"{title}: {stats.stars} stars, {stats.commits} commits")


def render_languages(languages: list[Language]) -> str:
    width, bar_x, bar_w = 350, 25, 300
    body = []
    x = float(bar_x)
    segments = []
    for lang in languages:
        w = bar_w * lang.percent / 100
        segments.append(f'<rect x="{x:.2f}" y="52" width="{w:.2f}" height="8" fill="{escape(lang.color)}"/>')
        x += w
    body.append(
        f'  <clipPath id="bar"><rect x="{bar_x}" y="52" width="{bar_w}" height="8" rx="4"/></clipPath>'
        f'<g clip-path="url(#bar)" class="fade">{"".join(segments)}</g>'
    )
    for i, lang in enumerate(languages):
        col, row = i % 2, i // 2
        lx, ly = 25 + col * 155, 88 + row * 26
        body.append(
            f'  <g class="fade" style="animation-delay:{150 + i * 90}ms">'
            f'<circle cx="{lx + 5}" cy="{ly - 4}" r="5" fill="{escape(lang.color)}"/>'
            f'<text x="{lx + 16}" y="{ly}" class="label" style="font-size:13px">{escape(lang.name)}</text>'
            f'<text x="{lx + 140}" y="{ly}" class="muted" text-anchor="end">{lang.percent:.1f}%</text></g>'
        )
    if not languages:
        body.append(f'  <text x="25" y="100" class="muted">No language data yet</text>')
    return card(width, 215, "Most Used Languages", "\n".join(body), "Most used languages")


def render_streak(stats: Stats, today: date) -> str:
    width = 700
    cols = [
        (117, number(stats.total_contributions), "Total Contributions", f"{stats.first_year} – Present"),
        (350, str(stats.current_streak.length), "Current Streak", date_range(stats.current_streak)),
        (583, str(stats.longest_streak.length), "Longest Streak", date_range(stats.longest_streak)),
    ]
    body = [
        f'  <line x1="233" y1="55" x2="233" y2="175" stroke="{BORDER}"/>',
        f'  <line x1="466" y1="55" x2="466" y2="175" stroke="{BORDER}"/>',
    ]
    for i, (x, value, label, sub) in enumerate(cols):
        delay = f'style="animation-delay:{150 + i * 150}ms"'
        if i == 1:
            body.append(
                f'  <g class="fade" {delay}>'
                f'<circle cx="{x}" cy="98" r="38" fill="none" stroke="{ACCENT}" stroke-width="5"/>'
                f'<text x="{x}" y="66" text-anchor="middle" style="font-size:20px">🔥</text>'
                f'<text x="{x}" y="108" class="value" text-anchor="middle" style="font-size:28px;fill:{TEXT}">{escape(value)}</text>'
                f'<text x="{x}" y="160" class="label" text-anchor="middle" style="fill:{ACCENT};font-weight:600">{label}</text>'
                f'<text x="{x}" y="180" class="muted" text-anchor="middle">{escape(sub)}</text></g>'
            )
        else:
            body.append(
                f'  <g class="fade" {delay}>'
                f'<text x="{x}" y="108" class="value" text-anchor="middle" style="font-size:28px">{escape(value)}</text>'
                f'<text x="{x}" y="160" class="label" text-anchor="middle">{label}</text>'
                f'<text x="{x}" y="180" class="muted" text-anchor="middle">{escape(sub)}</text></g>'
            )
    body.append(f'  <text x="{width - 20}" y="35" class="muted" text-anchor="end">Updated {today:%b} {today.day}, {today.year}</text>')
    return card(width, 200, "Contribution Streak", "\n".join(body), "Contribution streak")


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    login, out = argv[1], Path(argv[2])
    token = os.environ.get("GH_TOKEN")
    if not token:
        print("GH_TOKEN is not set", file=sys.stderr)
        return 2

    now = datetime.now(UTC)
    stats = summarize(fetch(login, token, now), now.date())
    out.mkdir(parents=True, exist_ok=True)
    name = os.environ.get("STATS_DISPLAY_NAME", login)
    (out / "stats.svg").write_text(render_stats(stats, name), encoding="utf-8")
    (out / "top-langs.svg").write_text(render_languages(stats.languages), encoding="utf-8")
    (out / "streak.svg").write_text(render_streak(stats, now.date()), encoding="utf-8")
    summary = (
        f"stars={stats.stars} commits={stats.commits} prs={stats.pull_requests} "
        f"issues={stats.issues} repos={stats.repos} contributions={stats.total_contributions} "
        f"current_streak={stats.current_streak.length} longest_streak={stats.longest_streak.length} "
        f"languages={', '.join(f'{lang.name} {lang.percent}%' for lang in stats.languages)}"
    )
    # In GitHub Actions, also show the numbers as an annotation on the run page.
    prefix = "::notice title=Profile stats::" if os.environ.get("GITHUB_ACTIONS") else ""
    print(prefix + summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
