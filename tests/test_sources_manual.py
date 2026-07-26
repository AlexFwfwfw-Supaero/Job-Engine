from jobhunt.sources.manual import posting_from_url


def test_posting_from_url_infers_title_from_the_first_line():
    text = "Senior GNSS Engineer\n\nWe are hiring in Munich."
    p = posting_from_url("https://example.com/job/9", text)
    assert p.title == "Senior GNSS Engineer"
    assert p.description == text
    assert p.source == "manual"
    assert p.url == "https://example.com/job/9"


def test_explicit_title_overrides_the_inferred_one():
    p = posting_from_url("https://example.com/j", "Some Heading\n\nbody", title="PNT Engineer")
    assert p.title == "PNT Engineer"


def test_blank_text_yields_an_empty_title_not_a_crash():
    p = posting_from_url("https://example.com/j", "")
    assert p.title == ""
    assert p.description == ""


def test_overlong_first_line_is_not_used_as_a_title():
    text = "x" * 300 + "\n\nbody"
    p = posting_from_url("https://example.com/j", text)
    assert p.title == ""
