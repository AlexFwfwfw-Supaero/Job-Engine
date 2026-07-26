from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from jobhunt.models import Deadline, Employer

REQUIRED_WEIGHTS = ("comp", "qol", "fit")


@dataclass
class RoleFamily:
    name: str
    weight: float
    keywords: list[str]


@dataclass
class ScoringConfig:
    weights: dict[str, float]
    qol_weights: dict[str, float]
    role_families: list[RoleFamily]
    negative_keywords: list[str] = field(default_factory=list)
    excluded_countries: list[str] = field(default_factory=list)
    known_languages: list[str] = field(default_factory=list)
    language_keywords: dict[str, list[str]] = field(default_factory=dict)
    staleness: dict[str, list[int]] = field(default_factory=dict)
    sunshine_range: list[int] = field(default_factory=lambda: [1300, 2900])
    rent_range: list[int] = field(default_factory=lambda: [400, 1800])


@dataclass
class City:
    name: str
    country: str
    sunshine_hours: float
    nature: float
    rent_index: float


@dataclass
class CompConfig:
    salary_by_country: dict[str, dict[str, float]]
    effective_tax: dict[str, float]
    pli: dict[str, float]
    reference_purchasing_power: float


def _read(path: Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_scoring(path: Path) -> ScoringConfig:
    raw = _read(path)
    weights = raw.get("weights", {})
    missing = [k for k in REQUIRED_WEIGHTS if k not in weights]
    if missing:
        raise ValueError(f"scoring config missing weights: {', '.join(missing)}")
    families = [
        RoleFamily(name=f["name"], weight=float(f.get("weight", 1.0)),
                   keywords=[k.lower() for k in f.get("keywords", [])])
        for f in raw.get("role_families", [])
    ]
    return ScoringConfig(
        weights={k: float(v) for k, v in weights.items()},
        qol_weights={k: float(v) for k, v in raw.get("qol_weights", {}).items()},
        role_families=families,
        negative_keywords=[k.lower() for k in raw.get("negative_keywords", [])],
        excluded_countries=raw.get("excluded_countries", []),
        known_languages=raw.get("known_languages", []),
        language_keywords={
            lang: [k.lower() for k in kws]
            for lang, kws in raw.get("language_keywords", {}).items()
        },
        staleness=raw.get("staleness", {}),
        sunshine_range=raw.get("sunshine_range", [1300, 2900]),
        rent_range=raw.get("rent_range", [400, 1800]),
    )


def load_cities(path: Path) -> dict[str, City]:
    raw = _read(path)
    out: dict[str, City] = {}
    for c in raw.get("cities", []):
        city = City(
            name=c["name"], country=c.get("country", ""),
            sunshine_hours=float(c.get("sunshine_hours", 0)),
            nature=float(c.get("nature", 0)),
            rent_index=float(c.get("rent_index", 0)),
        )
        out[city.name.lower()] = city
    return out


def load_comp(path: Path) -> CompConfig:
    raw = _read(path)
    return CompConfig(
        salary_by_country={
            country: {lvl: float(v) for lvl, v in levels.items()}
            for country, levels in raw.get("salary_by_country", {}).items()
        },
        effective_tax={k: float(v) for k, v in raw.get("effective_tax", {}).items()},
        pli={k: float(v) for k, v in raw.get("pli", {}).items()},
        reference_purchasing_power=float(raw.get("reference_purchasing_power", 40000)),
    )


def load_employers(path: Path) -> list[Employer]:
    raw = _read(path)
    return [
        Employer(
            name=e["name"], country=e.get("country", ""), city=e.get("city", ""),
            ats=e.get("ats", "manual"), ats_endpoint=e.get("ats_endpoint", ""),
            careers_url=e.get("careers_url", ""), tags=e.get("tags", []),
            poll_enabled=bool(e.get("poll_enabled", False)),
            notes=e.get("notes", ""),
        )
        for e in raw.get("employers", [])
    ]


def load_deadlines(path: Path) -> list[Deadline]:
    raw = _read(path)
    return [
        Deadline(
            name=d["name"], employer=d.get("employer", ""),
            opens=str(d["opens"]) if d.get("opens") else None,
            closes=str(d["closes"]) if d.get("closes") else None,
            url=d.get("url", ""), lead_days=int(d.get("lead_days", 42)),
            notes=d.get("notes", ""),
        )
        for d in raw.get("deadlines", [])
    ]
