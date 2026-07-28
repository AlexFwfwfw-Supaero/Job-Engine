"""Capgemini's own job API.

Every other module here covers an ATS platform used by many employers. This
one covers a single company, because Capgemini runs a bespoke service — the
page calls it "jobstream" — behind its careers site. It earns the exception:
Capgemini Engineering is one of the largest engineering consultancies in
Toulouse and Blagnac, and the API returns the **full posting text**, so its
jobs arrive already readable by the model with no second fetch.

`ats_endpoint` holds `country_code|brand`, matching the two filters the site
itself uses; the brand is optional and omitting it polls the whole country.
"""

from __future__ import annotations

from jobhunt.models import Employer
from jobhunt.sources.base import RawPosting

NAME = "capgemini"
API_URL = "https://cg-jobstream-api.azurewebsites.net/api/job-search"
DEFAULT_PAGE_SIZE = 100
DEFAULT_MAX_PAGES = 15


def parse_endpoint(endpoint: str) -> tuple[str, str]:
    """`'fr-fr|Capgemini Engineering'` -> `('fr-fr', 'Capgemini Engineering')`."""
    country, _, brand = (endpoint or "").partition("|")
    return country.strip(), brand.strip()


def _country(entry: dict, employer: Employer) -> str:
    """'fr-fr' -> 'FR'. The site's locale doubles as the country."""
    code = (entry.get("country_code") or "").split("-")[0]
    return code.upper() or employer.country


def parse_jobs(payload: dict, employer: Employer) -> list[RawPosting]:
    postings = []
    for entry in (payload or {}).get("data", []) or []:
        title = entry.get("title") or ""
        # "Lyon, Issy-les-Moulineaux, Cormelles-le-Royal" — one job, several
        # sites. The first is enough to score it.
        city = (entry.get("location") or "").split(",")[0].strip()
        body = entry.get("description_stripped") or ""
        postings.append(RawPosting(
            source=NAME,
            url=entry.get("apply_job_url") or employer.careers_url,
            title=title,
            employer_name=employer.name,
            city=city,
            country=_country(entry, employer),
            description=" · ".join(filter(None, [
                title, entry.get("experience_level") or "",
                entry.get("contract_type") or "", body,
            ])),
            external_id=str(entry.get("id", "")),
        ))
    return postings


def fetch(
    employer: Employer,
    client,
    max_pages: int = DEFAULT_MAX_PAGES,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> list[RawPosting]:
    if not employer.ats_endpoint:
        return []

    country, brand = parse_endpoint(employer.ats_endpoint)
    found: list[RawPosting] = []
    for page in range(1, max_pages + 1):
        params = {"country_code": country, "size": page_size, "page": page}
        if brand:
            params["brand"] = brand
        response = client.get(API_URL, params=params)
        response.raise_for_status()
        batch = parse_jobs(response.json(), employer)
        found.extend(batch)
        if len(batch) < page_size:
            break
    return found
