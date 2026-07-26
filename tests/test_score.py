import pytest

from jobhunt.config import City, CompConfig, RoleFamily, ScoringConfig
from jobhunt.score import compensation, quality_of_life, total_score


@pytest.fixture
def comp_cfg():
    return CompConfig(
        salary_by_country={"DE": {"junior": 60000, "phd": 33000},
                           "FR": {"junior": 40000}},
        effective_tax={"DE": 0.40, "FR": 0.25},
        pli={"DE": 1.00, "FR": 1.00},
        reference_purchasing_power=40000,
    )


@pytest.fixture
def cfg():
    return ScoringConfig(
        weights={"comp": 0.3, "qol": 0.2, "fit": 0.5},
        qol_weights={"sunshine": 0.5, "nature": 0.3, "rent": 0.2},
        role_families=[RoleFamily("gnss", 1.0, ["gnss"])],
        sunshine_range=[1500, 2500],
        rent_range=[500, 1500],
    )


def test_stated_salary_beats_the_estimate_table(comp_cfg, cfg):
    b = compensation("DE", "junior", 75000, comp_cfg, None, cfg)
    assert b.gross == 75000
    assert b.source == "stated"


def test_estimated_salary_used_when_none_stated(comp_cfg, cfg):
    b = compensation("DE", "junior", None, comp_cfg, None, cfg)
    assert b.gross == 60000
    assert b.source == "estimated"


def test_unknown_country_yields_unknown_not_zero(comp_cfg, cfg):
    b = compensation("JP", "junior", None, comp_cfg, None, cfg)
    assert b.source == "unknown"
    assert b.gross is None
    assert b.normalised == 0.0


def test_net_applies_the_effective_rate(comp_cfg, cfg):
    b = compensation("DE", "junior", 60000, comp_cfg, None, cfg)
    assert b.net == pytest.approx(36000)


def test_purchasing_power_divides_by_price_level(comp_cfg, cfg):
    comp_cfg.pli["DE"] = 1.20
    b = compensation("DE", "junior", 60000, comp_cfg, None, cfg)
    assert b.purchasing_power == pytest.approx(30000)


def test_purchasing_power_normalises_against_the_reference(comp_cfg, cfg):
    b = compensation("DE", "junior", 100000, comp_cfg, None, cfg)
    assert b.purchasing_power == pytest.approx(60000)
    assert b.normalised == 1.0  # clamped


def test_phd_level_uses_the_phd_row(comp_cfg, cfg):
    b = compensation("DE", "phd", None, comp_cfg, None, cfg)
    assert b.gross == 33000


def test_missing_level_for_known_country_is_unknown(comp_cfg, cfg):
    b = compensation("FR", "phd", None, comp_cfg, None, cfg)
    assert b.source == "unknown"


def test_quality_of_life_rewards_sun_nature_and_cheap_rent(cfg):
    sunny = City("Lisbon", "PT", sunshine_hours=2500, nature=10, rent_index=500)
    grey = City("Grey", "XX", sunshine_hours=1500, nature=0, rent_index=1500)
    assert quality_of_life(sunny, cfg) == pytest.approx(1.0)
    assert quality_of_life(grey, cfg) == pytest.approx(0.0)


def test_quality_of_life_clamps_outside_the_configured_range(cfg):
    extreme = City("Sun", "XX", sunshine_hours=4000, nature=99, rent_index=0)
    assert quality_of_life(extreme, cfg) == pytest.approx(1.0)


def test_unknown_city_scores_mid_not_zero(cfg):
    assert quality_of_life(None, cfg) == pytest.approx(0.5)


def test_total_is_the_weighted_sum(cfg):
    assert total_score(1.0, 0.0, 1.0, cfg.weights) == pytest.approx(0.8)
    assert total_score(0.0, 1.0, 0.0, cfg.weights) == pytest.approx(0.2)
