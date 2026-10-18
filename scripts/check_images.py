"""Fail if any image in a Markdown/HTML file doesn't load.

    python scripts/check_images.py README.md

Checks absolute http(s) image URLs from Markdown `![](...)`, `<img src>` and
`<source srcset>`. Relative paths must exist in the repository. Images on the
`output` branch are skipped when that branch hasn't been published yet.
"""

from __future__ import annotations

import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

PATTERNS = [
    r"!\[[^\]]*\]\(([^)\s]+)\)",
    r"<img[^>]+src=\"([^\"]+)\"",
    r"<source[^>]+srcset=\"([^\"]+)\"",
]


def image_urls(text: str) -> list[str]:
    found: list[str] = []
    for pattern in PATTERNS:
        for url in re.findall(pattern, text):
            # Only &amp; is decoded. html.unescape would also turn '&section=' into '§ion='.
            url = url.replace("&amp;", "&")
            if url not in found:
                found.append(url)
    return found


def check(url: str) -> tuple[bool, str]:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (profile-image-check)"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310 (checking links is the point)
            kind = response.headers.get("Content-Type", "")
            body = response.read(512)
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001 (report any network failure)
        return False, f"{type(exc).__name__}: {exc}"
    if kind.startswith("image/") or b"<svg" in body:
        return True, kind or "svg"
    return False, f"not an image ({kind})"


def main(argv: list[str]) -> int:
    path = Path(argv[1])
    failures = checked = skipped = 0
    in_actions = bool(os.environ.get("GITHUB_ACTIONS"))
    for url in image_urls(path.read_text(encoding="utf-8")):
        if not url.startswith(("http://", "https://")):
            ok, detail = (path.parent / url).exists(), "local file"
        else:
            ok, detail = check(url)
            if not ok and "/zeiado/output/" in url and "404" in detail:
                print(f"SKIP  {url}  (output branch not published yet)")
                skipped += 1
                continue
        print(f"{'OK  ' if ok else 'FAIL'}  {url}  ({detail})")
        if not ok and in_actions:
            print(f"::error title=Broken image::{url} ({detail})")
        failures += not ok
        checked += 1
    print(f"\n{failures} broken image(s)")
    if in_actions:
        print(f"::notice title=README images::{checked - failures} of {checked} load, {skipped} skipped")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
