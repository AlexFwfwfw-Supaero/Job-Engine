from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

from jobhunt.config import load_cities, load_comp, load_scoring
from jobhunt.links import apply_results, check_jobs
from jobhunt.match import evaluate
from jobhunt.models import Employer, Job, Stage
from jobhunt.poll import SOURCE_REGISTRY, poll_all
from jobhunt.rescore import rescore_all
from jobhunt.score import compensation, quality_of_life, total_score
from jobhunt.snapshot import fetch_text, save_snapshot
from jobhunt.sources.manual import posting_from_url
from jobhunt.store import Store
from jobhunt.web.deps import Deps
from jobhunt.web.views import (
    TABS, applied_context, archive_context, deadlines_context,
    interested_context, overview_context, render_tab, search_context,
)

CONTEXT_BUILDERS = {
    "overview": overview_context,
    "search": search_context,
    "interested": interested_context,
    "applied": applied_context,
    "archive": archive_context,
    "deadlines": deadlines_context,
}

KNOWN_TABS = {slug for slug, _, _ in TABS}
SEE_OTHER = 303


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_return(return_to: str) -> str:
    """Only ever redirect to a known tab.

    return_to arrives from a form field, so treating it as a URL would be an
    open redirect. Anything unrecognised falls back to the overview.
    """
    return f"/{return_to}" if return_to in KNOWN_TABS else "/overview"


def add_job_from_url(
    store: Store,
    deps: Deps,
    url: str,
    employer: str,
    city: str = "",
    country: str = "",
    level: str = "junior",
    tags: str = "",
    title: str | None = None,
    salary: float | None = None,
) -> int:
    """Fetch, snapshot, score and store a posting. Shared by the web UI and CLI."""
    cfg = load_scoring(deps.config_dir / "scoring.yaml")
    comp_cfg = load_comp(deps.config_dir / "comp.yaml")
    cities = load_cities(deps.config_dir / "cities.yaml")

    known = store.find_employer_by_name(employer)
    if known is None:
        employer_id = store.upsert_employer(
            Employer(name=employer, country=country, city=city)
        )
    else:
        employer_id = known.id
        country = country or known.country
        city = city or known.city

    client = deps.http_client()
    try:
        text = fetch_text(url, client)
    finally:
        close = getattr(client, "close", None)
        if close:
            close()

    posting = posting_from_url(url, text, title=title)
    postings_dir = Path(os.environ.get("JOBHUNT_POSTINGS", "data/postings"))
    snapshot_path = save_snapshot(postings_dir, url, text)

    match = evaluate(posting.title, posting.description, country, cfg)
    city_entry = cities.get(city.lower()) if city else None
    breakdown = compensation(country, level, salary, comp_cfg, city_entry, cfg)
    qol = quality_of_life(city_entry, cfg)
    total = total_score(breakdown.normalised, qol, match.role_fit, cfg.weights)

    now = _now()
    return store.upsert_job(Job(
        employer_id=employer_id, title=posting.title, url=url, city=city,
        country=country, source="manual", snapshot_path=str(snapshot_path),
        first_seen=now, last_seen=now, role_fit=match.role_fit,
        comp_score=breakdown.normalised, qol_score=qol, total_score=total,
        salary_stated=salary, level=level, language_flags=match.language_flags,
        tags=[t.strip() for t in tags.split(",") if t.strip()],
    ))


def create_app(deps: Deps) -> FastAPI:
    app = FastAPI(title="jobhunt")

    @app.get("/", response_class=RedirectResponse)
    def root() -> RedirectResponse:
        return RedirectResponse("/overview", status_code=SEE_OTHER)

    @app.get("/{slug}", response_class=HTMLResponse)
    def tab(slug: str) -> HTMLResponse:
        builder = CONTEXT_BUILDERS.get(slug)
        if builder is None:
            raise HTTPException(status_code=404, detail=f"no such tab: {slug}")
        store = deps.store_factory()
        try:
            return HTMLResponse(render_tab(slug, builder(store, deps)))
        finally:
            store.close()

    @app.post("/jobs")
    def create_job(
        url: str = Form(...),
        employer: str = Form(...),
        city: str = Form(""),
        country: str = Form(""),
        level: str = Form("junior"),
        tags: str = Form(""),
    ) -> RedirectResponse:
        store = deps.store_factory()
        try:
            add_job_from_url(store, deps, url, employer, city, country, level, tags)
        finally:
            store.close()
        return RedirectResponse("/search", status_code=SEE_OTHER)

    @app.post("/jobs/{job_id}/stage")
    def set_stage(
        job_id: int, stage: str = Form(...), return_to: str = Form("overview")
    ) -> RedirectResponse:
        try:
            parsed = Stage(stage)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"unknown stage: {stage}")
        store = deps.store_factory()
        try:
            store.set_stage(job_id, parsed, ts=_now())
        finally:
            store.close()
        return RedirectResponse(_safe_return(return_to), status_code=SEE_OTHER)

    @app.post("/jobs/{job_id}/note")
    def set_note(
        job_id: int, text: str = Form(""), return_to: str = Form("overview")
    ) -> RedirectResponse:
        store = deps.store_factory()
        try:
            store.set_note(job_id, text, ts=_now())
        finally:
            store.close()
        return RedirectResponse(_safe_return(return_to), status_code=SEE_OTHER)

    @app.post("/jobs/{job_id}/priority")
    def set_priority(
        job_id: int, value: int = Form(...), return_to: str = Form("overview")
    ) -> RedirectResponse:
        store = deps.store_factory()
        try:
            store.set_priority(job_id, value, ts=_now())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        finally:
            store.close()
        return RedirectResponse(_safe_return(return_to), status_code=SEE_OTHER)

    @app.post("/jobs/{job_id}/archive")
    def archive(
        job_id: int, reason: str = Form(""), return_to: str = Form("overview")
    ) -> RedirectResponse:
        store = deps.store_factory()
        try:
            store.archive_job(job_id, reason, ts=_now())
        finally:
            store.close()
        return RedirectResponse(_safe_return(return_to), status_code=SEE_OTHER)

    @app.post("/jobs/{job_id}/restore")
    def restore(job_id: int, return_to: str = Form("overview")) -> RedirectResponse:
        store = deps.store_factory()
        try:
            store.restore_job(job_id, ts=_now())
        finally:
            store.close()
        return RedirectResponse(_safe_return(return_to), status_code=SEE_OTHER)

    @app.post("/actions/rescore")
    def rescore(return_to: str = Form("search")) -> RedirectResponse:
        cfg = load_scoring(deps.config_dir / "scoring.yaml")
        comp_cfg = load_comp(deps.config_dir / "comp.yaml")
        cities = load_cities(deps.config_dir / "cities.yaml")
        store = deps.store_factory()
        try:
            rescore_all(store, cfg, comp_cfg, cities)
        finally:
            store.close()
        return RedirectResponse(_safe_return(return_to), status_code=SEE_OTHER)

    @app.post("/actions/refresh")
    def refresh(return_to: str = Form("search")) -> RedirectResponse:
        store = deps.store_factory()
        try:
            jobs = store.list_jobs()
            client = deps.http_client()
            try:
                results = check_jobs(jobs, client, now=_now())
            finally:
                close = getattr(client, "close", None)
                if close:
                    close()
            apply_results(store, results, now=_now())
        finally:
            store.close()
        return RedirectResponse(_safe_return(return_to), status_code=SEE_OTHER)

    @app.post("/actions/search")
    def search(return_to: str = Form("search")) -> RedirectResponse:
        cfg = load_scoring(deps.config_dir / "scoring.yaml")
        comp_cfg = load_comp(deps.config_dir / "comp.yaml")
        cities = load_cities(deps.config_dir / "cities.yaml")

        store = deps.store_factory()
        client = deps.http_client()
        try:
            poll_all(store, SOURCE_REGISTRY, client, cfg, comp_cfg, cities,
                     now=_now())
        finally:
            close = getattr(client, "close", None)
            if close:
                close()
            store.close()
        return RedirectResponse(_safe_return(return_to), status_code=SEE_OTHER)

    return app
