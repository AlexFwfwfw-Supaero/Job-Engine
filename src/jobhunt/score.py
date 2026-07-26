from __future__ import annotations

from dataclasses import dataclass

from jobhunt.config import City, CompConfig, ScoringConfig

UNKNOWN_CITY_QOL = 0.5


@dataclass
class CompBreakdown:
    gross: float | None
    net: float | None
    purchasing_power: float | None
    source: str  # "stated" | "estimated" | "unknown"
    normalised: float


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _scale(value: float, low: float, high: float) -> float:
    if high == low:
        return 0.0
    return _clamp((value - low) / (high - low))


def compensation(
    country: str,
    level: str,
    salary_stated: float | None,
    comp: CompConfig,
    city: City | None,
    cfg: ScoringConfig,
) -> CompBreakdown:
    """Resolve gross, net, and purchasing-power-adjusted pay for a posting.

    Stated salary always wins. When neither a stated figure nor a table entry
    exists the result is 'unknown' with a zero contribution — the dashboard
    renders that as 'no data', never as a low salary.
    """
    country = (country or "").upper()

    if salary_stated:
        gross, source = float(salary_stated), "stated"
    else:
        gross = comp.salary_by_country.get(country, {}).get(level)
        source = "estimated" if gross is not None else "unknown"

    if gross is None:
        return CompBreakdown(None, None, None, "unknown", 0.0)

    tax = comp.effective_tax.get(country)
    if tax is None:
        return CompBreakdown(gross, None, None, source, 0.0)
    net = gross * (1.0 - tax)

    pli = comp.pli.get(country, 1.0)
    purchasing_power = net / pli if pli else net
    normalised = _clamp(purchasing_power / comp.reference_purchasing_power)

    return CompBreakdown(gross, net, purchasing_power, source, normalised)


def quality_of_life(city: City | None, cfg: ScoringConfig) -> float:
    """Weighted sun / nature / affordability score in [0, 1].

    An unknown city scores mid-range rather than zero, so a missing entry in
    cities.yaml does not silently bury an otherwise strong role.
    """
    if city is None:
        return UNKNOWN_CITY_QOL

    sun_low, sun_high = cfg.sunshine_range
    rent_low, rent_high = cfg.rent_range

    sunshine = _scale(city.sunshine_hours, sun_low, sun_high)
    nature = _clamp(city.nature / 10.0)
    rent = 1.0 - _scale(city.rent_index, rent_low, rent_high)

    weights = cfg.qol_weights
    total = weights.get("sunshine", 0) + weights.get("nature", 0) + weights.get("rent", 0)
    if total == 0:
        return UNKNOWN_CITY_QOL

    return (
        sunshine * weights.get("sunshine", 0)
        + nature * weights.get("nature", 0)
        + rent * weights.get("rent", 0)
    ) / total


def total_score(
    comp_norm: float, qol: float, role_fit: float, weights: dict[str, float]
) -> float:
    total = weights.get("comp", 0) + weights.get("qol", 0) + weights.get("fit", 0)
    if total == 0:
        return 0.0
    return (
        comp_norm * weights.get("comp", 0)
        + qol * weights.get("qol", 0)
        + role_fit * weights.get("fit", 0)
    ) / total
