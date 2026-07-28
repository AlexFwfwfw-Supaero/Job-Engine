"""RSS job feeds (DLR).

The last resort. DLR's SuccessFactors tenant renders everything in
JavaScript — /search/, /searchjobs/ and /go/ all return a page with no job in
it — so the only machine-readable view is the category RSS feed. That feed is
capped at the ten newest postings, which makes it a change detector rather
than a complete source, and the entry in employers.yaml has to say so.

It earns its place anyway: DLR houses the Institute of Communications and
Navigation, and the feed carries the full advert text, so what it does catch
arrives readable by the model with no second fetch.
"""

from jobhunt.models import Employer
from jobhunt.sources import rss

FEED = """<?xml version="1.0"?>
<rss version="2.0"><channel>
<title>dlrdeutsch - Alle Stellen</title>
<item>
  <title><![CDATA[Doktorand/in (w/m/d) - GNSS-Signalverarbeitung (Oberpfaffenhofen)]]></title>
  <link>https://jobs.dlr.de/job/Oberpfaffenhofen-Doktorand/1234567/</link>
  <description><![CDATA[<p>Am Institut f&uuml;r Kommunikation und Navigation
    entwickeln wir Verfahren zur robusten Positionsbestimmung.</p>]]></description>
  <pubDate>Mon, 27 Jul 2026 08:00:00 GMT</pubDate>
</item>
<item>
  <title><![CDATA[Projektleitung Windkanalversuche (m/w/d) (Göttingen)]]></title>
  <link>https://jobs.dlr.de/job/Goettingen-Projektleitung/7654321/</link>
  <description><![CDATA[Windkanal.]]></description>
</item>
</channel></rss>
"""


def employer(**kw):
    base = dict(name="DLR", ats="rss", country="DE",
                ats_endpoint="https://jobs.dlr.de/services/rss/category/?catid=9261201",
                careers_url="https://jobs.dlr.de/")
    base.update(kw)
    return Employer(**base)


def test_every_item_becomes_a_posting():
    assert len(rss.parse_jobs(FEED, employer())) == 2


def test_the_city_is_taken_out_of_the_title():
    """The feed has no location field; the site puts it in brackets at the
    end of the title, which is the only place it appears."""
    posting = rss.parse_jobs(FEED, employer())[0]
    assert posting.city == "Oberpfaffenhofen"
    assert posting.title == "Doktorand/in (w/m/d) - GNSS-Signalverarbeitung"


def test_a_gendered_bracket_is_not_mistaken_for_a_city():
    """German postings carry '(w/m/d)' mid-title. Only the trailing bracket
    is a location."""
    posting = rss.parse_jobs(FEED, employer())[1]
    assert posting.city == "Göttingen"
    assert "(m/w/d)" in posting.title


def test_a_title_with_no_bracket_keeps_its_whole_title():
    feed = FEED.replace(" (Oberpfaffenhofen)", "")
    posting = rss.parse_jobs(feed, employer())[0]
    assert posting.city == ""
    assert posting.title.endswith("GNSS-Signalverarbeitung")


def test_the_advert_text_comes_through_as_text():
    posting = rss.parse_jobs(FEED, employer())[0]
    assert "Kommunikation und Navigation" in posting.description
    assert "<p>" not in posting.description


def test_the_link_and_id_are_kept():
    posting = rss.parse_jobs(FEED, employer())[0]
    assert posting.url == "https://jobs.dlr.de/job/Oberpfaffenhofen-Doktorand/1234567/"
    assert posting.external_id == "1234567"


def test_the_country_is_the_employers_own():
    """The feed carries no country. DLR is a German institution on German
    sites, so the employer's own country is the honest default."""
    assert rss.parse_jobs(FEED, employer())[0].country == "DE"


def test_an_empty_feed_is_not_an_error():
    assert rss.parse_jobs(
        '<?xml version="1.0"?><rss><channel><title>x</title></channel></rss>',
        employer()) == []


def test_malformed_xml_is_an_error_not_a_quiet_empty_poll():
    import pytest

    with pytest.raises(ValueError):
        rss.parse_jobs("<rss><channel", employer())


class FakeResponse:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        return None


def test_fetch_reads_the_configured_feed():
    seen = {}

    class Client:
        def get(self, url):
            seen["url"] = url
            return FakeResponse(FEED)

    assert len(rss.fetch(employer(), Client())) == 2
    assert seen["url"].endswith("catid=9261201")


def test_fetch_without_an_endpoint_asks_for_nothing():
    class Client:
        def get(self, url):
            raise AssertionError("should not be called")

    assert rss.fetch(employer(ats_endpoint=""), Client()) == []
