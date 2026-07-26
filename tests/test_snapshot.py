import pytest

from jobhunt.snapshot import fetch_text, html_to_text, save_snapshot


class FakeResponse:
    def __init__(self, text, status=200):
        self.text = text
        self.status = status

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")


class FakeClient:
    def __init__(self, response):
        self._response = response
        self.calls = []

    def get(self, url):
        self.calls.append(url)
        return self._response


def test_html_to_text_strips_tags():
    html = "<html><body><h1>GNSS Engineer</h1><p>Join us.</p></body></html>"
    assert html_to_text(html) == "GNSS Engineer\n\nJoin us."


def test_html_to_text_drops_script_and_style_content():
    html = "<body><script>var x=1;</script><style>p{color:red}</style><p>Real</p></body>"
    assert html_to_text(html) == "Real"


def test_html_to_text_unescapes_entities():
    assert html_to_text("<p>R&amp;S &mdash; Munich</p>") == "R&S — Munich"


def test_html_to_text_collapses_blank_runs():
    assert html_to_text("<p>a</p><p></p><p></p><p>b</p>") == "a\n\nb"


def test_fetch_text_uses_the_injected_client():
    client = FakeClient(FakeResponse("<p>Hello</p>"))
    assert fetch_text("https://example.com/job", client) == "Hello"
    assert client.calls == ["https://example.com/job"]


def test_fetch_text_raises_on_http_error():
    client = FakeClient(FakeResponse("nope", status=404))
    with pytest.raises(RuntimeError):
        fetch_text("https://example.com/missing", client)


def test_save_snapshot_writes_a_stable_path_per_url(tmp_path):
    first = save_snapshot(tmp_path, "https://example.com/job/1", "content one")
    second = save_snapshot(tmp_path, "https://example.com/job/1", "content two")
    assert first == second
    assert first.read_text(encoding="utf-8") == "content two"
    assert first.suffix == ".md"


def test_save_snapshot_separates_different_urls(tmp_path):
    a = save_snapshot(tmp_path, "https://example.com/a", "a")
    b = save_snapshot(tmp_path, "https://example.com/b", "b")
    assert a != b


def test_save_snapshot_creates_the_directory(tmp_path):
    target = tmp_path / "nested" / "postings"
    path = save_snapshot(target, "https://example.com/x", "x")
    assert path.exists()
