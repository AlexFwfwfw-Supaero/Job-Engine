import pytest

from jobhunt.config import RoleFamily, ScoringConfig
from jobhunt.match import evaluate


@pytest.fixture
def cfg():
    return ScoringConfig(
        weights={"comp": 0.3, "qol": 0.2, "fit": 0.5},
        qol_weights={"sunshine": 0.5, "nature": 0.3, "rent": 0.2},
        role_families=[
            RoleFamily("gnss", 1.0, ["gnss", "galileo", "receiver"]),
            RoleFamily("radar", 0.8, ["radar", "sar"]),
            RoleFamily("adjacent", 0.5, ["digital signal processing"]),
        ],
        negative_keywords=["land surveyor", "sales representative"],
        excluded_countries=["GB"],
        known_languages=["en", "fr", "es", "pt"],
        language_keywords={"de": ["fluent german"], "nl": ["fluent dutch"]},
    )


def test_excluded_country_is_rejected(cfg):
    r = evaluate("GNSS Engineer", "great job", "GB", cfg)
    assert r.relevant is False
    assert any("GB" in reason for reason in r.reasons)


def test_negative_keyword_in_title_is_rejected(cfg):
    r = evaluate("Land Surveyor", "gnss equipment", "DE", cfg)
    assert r.relevant is False
    assert any("land surveyor" in reason for reason in r.reasons)


def test_negative_keyword_only_in_description_is_not_rejected(cfg):
    r = evaluate("GNSS Engineer", "you will support our sales representative", "DE", cfg)
    assert r.relevant is True


def test_title_match_scores_higher_than_description_match(cfg):
    in_title = evaluate("GNSS Receiver Engineer", "unrelated text", "DE", cfg)
    in_desc = evaluate("Systems Engineer", "work on gnss receiver design", "DE", cfg)
    assert in_title.role_fit > in_desc.role_fit


def test_role_fit_uses_the_best_family_weighted(cfg):
    gnss = evaluate("GNSS Engineer", "", "DE", cfg)
    adjacent = evaluate("Digital Signal Processing Engineer", "", "DE", cfg)
    assert gnss.role_fit > adjacent.role_fit
    assert gnss.matched_families == ["gnss"]


def test_role_fit_is_clamped_to_unit_interval(cfg):
    r = evaluate("GNSS Galileo Receiver Engineer",
                 "gnss galileo receiver gnss galileo receiver", "DE", cfg)
    assert 0.0 <= r.role_fit <= 1.0


def test_no_match_is_irrelevant_but_scored_zero(cfg):
    r = evaluate("Accountant", "bookkeeping", "DE", cfg)
    assert r.role_fit == 0.0
    assert r.relevant is False
    assert any("no role family" in reason for reason in r.reasons)


def test_unknown_language_is_flagged_not_rejected(cfg):
    r = evaluate("GNSS Engineer", "fluent German required", "DE", cfg)
    assert r.relevant is True
    assert r.language_flags == ["de"]


def test_known_language_is_not_flagged(cfg):
    cfg.language_keywords["fr"] = ["fluent french"]
    r = evaluate("GNSS Engineer", "fluent French required", "FR", cfg)
    assert r.language_flags == []


def test_matching_is_case_insensitive(cfg):
    r = evaluate("GALILEO SIGNAL ENGINEER", "", "DE", cfg)
    assert r.role_fit > 0
