from __future__ import annotations

import hashlib
import re
from html.parser import HTMLParser
from pathlib import Path

USER_AGENT = "jobhunt/0.1 (personal job search tool; contact via the operator)"
IGNORED_TAGS = {"script", "style", "head", "noscript"}
BLOCK_TAGS = {
    "p", "div", "br", "li", "tr", "section", "article",
    "h1", "h2", "h3", "h4", "h5", "h6",
}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in IGNORED_TAGS:
            self._skip_depth += 1
        elif tag in BLOCK_TAGS:
            self.parts.append("\n\n")

    def handle_endtag(self, tag):
        if tag in IGNORED_TAGS and self._skip_depth:
            self._skip_depth -= 1
        elif tag in BLOCK_TAGS:
            self.parts.append("\n\n")

    def handle_data(self, data):
        if self._skip_depth == 0:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    """Reduce an HTML page to readable plain text.

    Deliberately crude: snapshots exist so the posting text survives the
    posting being taken down, not to reproduce the page.
    """
    parser = _TextExtractor()
    parser.feed(html)
    text = "".join(parser.parts)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def fetch_text(url: str, client) -> str:
    """Fetch a URL through an injected client and return it as plain text."""
    response = client.get(url)
    response.raise_for_status()
    return html_to_text(response.text)


def save_snapshot(directory: Path, url: str, text: str) -> Path:
    """Write a snapshot to a path derived from the URL, so re-saving overwrites."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    path = directory / f"{digest}.md"
    path.write_text(text, encoding="utf-8")
    return path


def default_client():
    """Build the real HTTP client. Never called from tests."""
    import httpx

    return httpx.Client(
        headers={"User-Agent": USER_AGENT}, timeout=20.0, follow_redirects=True
    )
