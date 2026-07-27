from urllib.parse import parse_qs, urlparse

from jobhunt.config import City, RoleFamily, ScoringConfig
from jobhunt.linkedin import (
    SearchLink,
    build_links,
    locations_from_cities,
    search_url,
)


def city(name, country):
    return City(name=name, country=country, sunshine_hours=1800,
                nature=5, rent_index=900)


def cfg(terms):
    return ScoringConfig(
        weights={"comp": 0.3, "qol": 0.2, "fit": 0.5},
        qol_weights={"sunshine": 0.5, "nature": 0.3, "rent": 0.2},
        role_families=[RoleFamily("gnss", 1.0, ["gnss"])],
        poll_search_terms=terms,
    )


def _params(url):
    return parse_qs(urlparse(url).query)


def test_search_url_encodes_keywords_and_location():
    url = search_url("sensor fusion", "Toulouse, France")
    assert url.startswith("https://www.linkedin.com/jobs/search/?")
    params = _params(url)
    assert params["keywords"] == ["sensor fusion"]
    assert params["location"] == ["Toulouse, France"]


def test_search_url_requests_recent_postings_only():
    """A link that returns three-year-old posts is worse than no link."""
    params = _params(search_url("gnss", "Munich, Germany"))
    assert params["f_TPR"] == ["r604800"]
    assert params["sortBy"] == ["DD"]


def test_search_url_without_location_omits_the_parameter():
    assert "location" not in _params(search_url("gnss", ""))


def test_build_links_crosses_every_term_with_every_location():
    links = build_links(cfg(["gnss", "radar"]), ["Toulouse, France", "Munich, Germany"])
    assert len(links) == 4
    assert all(isinstance(link, SearchLink) for link in links)
    assert {link.term for link in links} == {"gnss", "radar"}
    assert {link.location for link in links} == {"Toulouse, France", "Munich, Germany"}


def test_build_links_labels_each_link_for_a_human():
    (link,) = build_links(cfg(["gnss"]), ["Toulouse, France"])
    assert link.label == "gnss — Toulouse, France"


def test_build_links_with_no_locations_still_produces_one_link_per_term():
    links = build_links(cfg(["gnss", "pnt"]), [])
    assert [link.location for link in links] == ["", ""]
    assert len(links) == 2


def test_build_links_is_empty_when_no_search_terms_are_configured():
    assert build_links(cfg([]), ["Toulouse, France"]) == []


def test_locations_use_country_names_not_iso_codes():
    """LinkedIn resolves 'Toulouse, France'; 'Toulouse, FR' matches nothing."""
    assert locations_from_cities({"toulouse": city("Toulouse", "FR")}) == [
        "Toulouse, France"
    ]


def test_locations_skip_cities_with_an_unmapped_country():
    cities = {"munich": city("Munich", "DE"), "nowhere": city("Nowhere", "ZZ")}
    assert locations_from_cities(cities) == ["Munich, Germany"]


def test_locations_are_sorted_and_deduplicated():
    cities = {
        "toulouse": city("Toulouse", "FR"),
        "munich": city("Munich", "DE"),
        "munich2": city("Munich", "DE"),
    }
    assert locations_from_cities(cities) == ["Munich, Germany", "Toulouse, France"]
