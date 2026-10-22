"""FastAPI application: streaming answers, source search, record drawer, metadata.

Public by default: anyone may ask. Model-written briefings cost money, so they are
limited per visitor and overall, and identical requests are served from a cache.
Invite-only sign-in remains available (``[auth] required = true``)."""

from __future__ import annotations

import json
import logging
import os
import queue
import re
import secrets
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Iterator
from datetime import date
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationInfo, field_validator

from hansard.api.auth import (
    COOKIE,
    LoginThrottle,
    authenticate,
    create_session,
    end_session,
    user_for_token,
)
from hansard.api.briefings import (
    ID_PATTERN,
    feature,
    featured_list,
    fmt_date,
    get_briefing,
    is_featured,
    keep,
    page_with_meta,
    public_view,
    recent_briefings,
    summary_text,
    unfeature,
    withdraw_briefing,
)
from hansard.api.cards import card_png
from hansard.api.feeds import FEED_TYPE, rss
from hansard.api.plaintext import (
    bill_text,
    briefing_text,
    committee_text,
    debate_text,
    devolved_day_text,
    edm_text,
    evidence_text,
    group_text,
    inquiry_text,
    person_text,
    week_text,
    with_text,
)
from hansard.api.reports import (
    CLOSED,
    DAILY_LIMIT,
    add_report,
    close_report,
    corrections,
    list_reports,
    recent_count,
)
from hansard.api.usage import (
    InFlight,
    RateLimiter,
    alerts,
    cache_key,
    cached_events,
    check_caps,
    count_question,
    mark_supporter,
    network_of,
    popular_questions,
    recent_failures,
    record_event,
    record_failure,
    record_feedback,
    record_usage,
    record_visit,
    store_events,
    summary,
    support_reminder_due,
    task_summary,
    visit_summary,
    visitor_key,
)
from hansard.config import REPO_ROOT, Settings, load_settings
from hansard.devolution import notes_for
from hansard.engine import Engine, Scope
from hansard.rag import prompts
from hansard.rag.interests import without_public_office
from hansard.rag.pipeline import AskRequest, Pipeline
from hansard.rag.query_parse import ITL1_REGIONS, QueryPlan, parse_model
from hansard.reference.portfolios import PORTFOLIOS
from hansard.store.state import StateDB

log = logging.getLogger(__name__)

# a cached briefing is only reused while the instructions that wrote it are unchanged
INSTRUCTIONS_VERSION = __import__("hashlib").sha256(
    (prompts.ANSWER_SYSTEM + json.dumps([prompts.MODE_INSTRUCTIONS, prompts.GROUP_BY_INSTRUCTIONS],
                                        sort_keys=True)).encode()).hexdigest()[:12]

ATTRIBUTION = [
    {"text": "Contains Parliamentary information licensed under the Open Parliament Licence v3.0.",
     "url": "https://www.parliament.uk/site-information/copyright-parliament/open-parliament-licence/"},
    {"text": "Contains public sector information licensed under the Open Government Licence v3.0 "
             "(ONS, NRS and NISRA boundaries, urban areas and census populations).",
     "url": "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/"},
    {"text": "Hansard data and member records via TheyWorkForYou / ParlParse, with thanks to mySociety.",
     "url": "https://www.mysociety.org/"},
]


class LoginBody(BaseModel):
    email: str
    password: str


class ScopeBody(BaseModel):
    written: bool = True
    pbc: bool = True
    votes: bool = True
    edms: bool = True
    devolved: bool = True
    evidence: bool = True


class FeedbackBody(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    useful: bool
    briefing_key: str | None = Field(default=None, max_length=128)


class ReportBody(BaseModel):
    kind: Literal["correction", "complaint", "general"]
    message: str = Field(min_length=10, max_length=4000)
    briefing_id: str | None = Field(default=None, max_length=20)
    contact: str | None = Field(default=None, max_length=200)
    website: str | None = Field(default=None, max_length=200)  # left empty by people; filled in by spam bots


class CloseReportBody(BaseModel):
    status: Literal["resolved", "dismissed"]
    resolution: str = Field(min_length=3, max_length=2000)       # private
    public_note: str | None = Field(default=None, max_length=1000)  # shown on the corrections page


class WithdrawBody(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class FeatureBody(BaseModel):
    briefing: str = Field(min_length=10, max_length=300, description="the briefing's id or full address")
    title: str | None = Field(default=None, max_length=200)
    blurb: str | None = Field(default=None, max_length=600)


class HitBody(BaseModel):
    path: str = Field(min_length=1, max_length=200)
    referrer: str | None = Field(default=None, max_length=500)
    # something done on the page rather than a page view: "source" (opened a source)
    event: Literal["source"] | None = None


class HistoryItem(BaseModel):
    """An earlier question in the conversation, as the browser sends it back. Each part is cut to a fixed length
    (longer than the browser ever sends), so a request cannot inflate the prompt."""

    question: str = ""
    answer: str = ""
    topic: str = ""
    scope: str = ""

    @field_validator("question", "answer", "topic", "scope", mode="before")
    @classmethod
    def _trimmed(cls, v: Any, info: ValidationInfo) -> str:
        return str(v or "")[:HISTORY_LIMITS[info.field_name]]


HISTORY_LIMITS = {"question": 2000, "answer": 1000, "topic": 500, "scope": 1000}
PLAN_MAX_CHARS = 20_000  # an edited plan is a few hundred characters; this is far more than any reader needs


class AskBody(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    mode: Literal["summary", "comprehensive"] = "summary"
    recency: float | None = Field(default=None, ge=0, le=1)
    role_weight: float | None = Field(default=None, ge=1, le=2)
    scope: ScopeBody = ScopeBody()
    plan: dict[str, Any] | None = None
    history: list[HistoryItem] = Field(default_factory=list, max_length=10)
    sources_only: bool = False  # find and show the sources, but write no briefing (free)
    follows: str | None = Field(default=None, max_length=2000)  # a follow-up: the question it follows (kept with it)
    compare: list[dict[str, str]] = Field(default_factory=list, max_length=3)  # sides, from the Compare page

    @field_validator("plan")
    @classmethod
    def _plan_size(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        if v is not None and len(json.dumps(v, default=str)) > PLAN_MAX_CHARS:
            raise ValueError("the search plan is too large")
        return v

    @field_validator("compare")
    @classmethod
    def _compare_trimmed(cls, v: list[dict[str, str]]) -> list[dict[str, str]]:
        return [{k[:20]: str(x)[:200] for k, x in list(side.items())[:5]} for side in v]

    def history_dicts(self) -> list[dict[str, str]]:
        return [h.model_dump() for h in self.history]


def donate_url(settings: Settings) -> str | None:
    """The 'Support this site' link, if one is set and is a plain https address."""
    url = (os.environ.get("HANSARD_DONATE_URL") or settings.section("site").get("donate_url") or "").strip()
    return url if url.startswith("https://") and len(url) < 300 and not any(c in url for c in "\"'<> ") else None


def _public_run(run: dict[str, Any]) -> dict[str, Any]:
    """What visitors may see of an update run: when it finished and whether it worked (no internal details)."""
    return {"finished_at": run.get("finished_at"), "status": run.get("status")}


def server_timing(timings: dict[str, float]) -> str:
    """'keyword;dur=812.4, meaning;dur=40.1': how long each stage took, readable in the browser's network panel."""
    return ", ".join(f"{k};dur={v * 1000:.1f}" for k, v in timings.items())


KEEP_ALIVE_SECONDS = 15


def sse(events: Iterator[dict[str, Any]], keep_alive: float = KEEP_ALIVE_SECONDS) -> Iterator[bytes]:
    """Server-sent events, with a comment line after every few seconds of silence (while the model is thinking, say):
    Cloudflare closes a response that sends nothing for 100 seconds. The events are worked out in a thread of their
    own, which stops at the next event once the reader has gone."""
    q: queue.Queue = queue.Queue()
    gone = threading.Event()
    end = object()

    def work() -> None:
        try:
            for ev in events:
                q.put(ev)
                if gone.is_set():
                    break
        except Exception as exc:  # passed on, to be raised where the response is being sent
            q.put(exc)
        finally:
            if hasattr(events, "close"):
                events.close()  # here, in the thread that ran it (a briefing being written stops)
            q.put(end)

    threading.Thread(target=work, name="sse", daemon=True).start()
    try:
        while True:
            try:
                ev = q.get(timeout=keep_alive)
            except queue.Empty:
                yield b": still working\n\n"
                continue
            if ev is end:
                return
            if isinstance(ev, Exception):
                raise ev
            yield f"event: {ev['type']}\ndata: {json.dumps(ev, default=str)}\n\n".encode()
    finally:
        gone.set()


def create_app(settings: Settings | None = None, pipeline: Pipeline | None = None,
               state: StateDB | None = None) -> FastAPI:
    settings = settings or load_settings()
    state = state or StateDB(settings.state_db)
    app = FastAPI(title="Where They Stand", docs_url="/api/docs", openapi_url="/api/openapi.json")
    throttle = LoginThrottle()
    address_throttle = LoginThrottle(limit=20)  # failed sign-ins from one address, whatever email is tried
    u = settings.usage
    inflight = InFlight(int(u.get("briefings_at_once_per_reader", 2)), int(u.get("briefings_at_once", 8)),
                        float(u.get("briefing_estimate_usd", 0.15)))
    auth_required = bool(settings.section("auth").get("required", False))
    limiter = RateLimiter(int(settings.usage.get("requests_per_minute", 20)))
    hit_limiter = RateLimiter(120)  # page counts: their own allowance, so browsing never uses up the one for questions
    cache_hours = float(settings.usage.get("answer_cache_hours", 24))
    admin_token = os.environ.get("HANSARD_ADMIN_TOKEN") or None
    lock = threading.Lock()
    dist = Path(settings.section("paths").get("web_dist", REPO_ROOT / "web" / "dist"))
    holder: dict[str, Pipeline] = {}
    if pipeline is not None:
        holder["p"] = pipeline

    # Answers that change only when the record or member data is updated (twice a day, by a separate process) are
    # kept for ten minutes, so a page opened again, or by the next reader, comes straight from memory.
    remembered_answers: OrderedDict[str, tuple[float, Any]] = OrderedDict()
    remembered_lock = threading.Lock()

    def remembered(key: str, compute: Callable[[], Any], ttl: float = 600.0) -> Any:
        now = time.time()
        with remembered_lock:
            hit = remembered_answers.get(key)
        if hit is not None and now - hit[0] < ttl:
            return hit[1]
        value = compute()
        with remembered_lock:
            remembered_answers[key] = (now, value)
            remembered_answers.move_to_end(key)
            while len(remembered_answers) > 2000:
                remembered_answers.popitem(last=False)
        return value

    def readable(build: Callable[[], str]) -> str | None:
        """The page's readable text, or none if it cannot be built (the page itself still works)."""
        try:
            return build()
        except Exception:  # noqa: BLE001 - the readable copy is a courtesy; never fail the page for it
            log.exception("readable page text not built")
            return None

    def remembered_if_ready(key: str, ttl: float = 600.0) -> Any:
        with remembered_lock:
            hit = remembered_answers.get(key)
        return hit[1] if hit is not None and time.time() - hit[0] < ttl else None

    def get_pipeline() -> Pipeline:
        with lock:
            if "p" not in holder:
                holder["p"] = Pipeline(settings, Engine(settings))
            return holder["p"]

    def get_people():
        from hansard.people import People

        p = get_pipeline()
        with lock:
            if "people" not in holder:
                holder["people"] = People(settings, p.engine.resolver, p.today)
            return holder["people"]

    def current_user(request: Request) -> dict[str, Any] | None:
        user = user_for_token(state, request.cookies.get(COOKIE))
        if user is None and auth_required:
            raise HTTPException(401, "Please sign in.")
        return user

    def admin_user(request: Request, user=Depends(current_user)):
        if user and user["is_admin"]:
            return user
        given = request.headers.get("authorization", "")
        if admin_token and secrets.compare_digest(given.encode(), f"Bearer {admin_token}".encode()):
            return None
        raise HTTPException(403, "Admins only.")

    def requester(request: Request, user: dict[str, Any] | None) -> tuple[str | None, str]:
        """(visitor hash for anonymous people, rate-limit key); 429 when over the per-minute limit."""
        visitor = None if user else visitor_key(state, request.client.host if request.client else None)
        key = f"user:{user['user_id']}" if user else f"visitor:{visitor}"
        if not limiter.allow(key):
            raise HTTPException(429, "Too many requests. Please wait a minute and try again.")
        return visitor, key

    # ---- auth --------------------------------------------------------------------
    @app.post("/api/auth/login")
    def login(body: LoginBody, request: Request, response: Response):
        address = network_of(request.client.host if request.client else None)
        key = f"{address}|{body.email.lower()}"
        # per address and email, and per address whatever email is tried
        if throttle.blocked(key) or address_throttle.blocked(address):
            raise HTTPException(429, "Too many failed sign-in attempts. Try again in 15 minutes.")
        user = authenticate(state, body.email, body.password)
        if user is None:
            throttle.fail(key)
            address_throttle.fail(address)
            raise HTTPException(401, "Incorrect email or password.")
        throttle.reset(key)
        days = int(settings.section("auth").get("session_days", 14))
        token = create_session(state, user["user_id"], days)
        response.set_cookie(COOKIE, token, max_age=days * 86400, httponly=True, samesite="lax",
                            secure=request.url.scheme == "https", path="/")
        return {"email": user["email"], "is_admin": bool(user["is_admin"])}

    @app.post("/api/auth/logout")
    def logout(request: Request, response: Response):
        end_session(state, request.cookies.get(COOKIE))
        response.delete_cookie(COOKIE, path="/")
        return {"ok": True}

    @app.get("/api/me")
    def me(user=Depends(current_user)):
        return {"email": user["email"] if user else None, "is_admin": bool(user and user["is_admin"]),
                "auth_required": auth_required}

    # ---- metadata ----------------------------------------------------------------
    @app.get("/api/meta")
    def meta(user=Depends(current_user)):
        p = get_pipeline()
        idx = p.engine.index.stats()
        runs = state.runs(1)
        return {
            "regions": ITL1_REGIONS,
            "region_groups": settings.regions,
            "portfolios": PORTFOLIOS,
            "parties": [{"id": i, "name": n} for i, n in p.party_choices()],
            "answers_enabled": p.has_model(),
            "model": settings.answer.get("model"),
            "archive_start": settings.archive_start.isoformat(),
            "index": idx,
            "last_sync": _public_run(runs[0]) if runs else None,
            "defaults": {"recency": settings.ranking.get("default_recency", 0.5),
                         "role_weight": settings.ranking.get("default_role_weight", 1.5)},
            "attribution": ATTRIBUTION,
            "donate_url": donate_url(settings),
        }

    @app.get("/api/site")
    def site():
        """Small, public settings for the static pages."""
        return {"donate_url": donate_url(settings)}

    @app.post("/api/support-clicked")
    def support_clicked(request: Request, user=Depends(current_user)):
        """The reader went through to the donation page from the reminder: no more reminders for them today."""
        visitor, _ = requester(request, user)
        if visitor:
            mark_supporter(state, visitor)
        return {"ok": True}

    @app.get("/api/health")
    def health():
        return {"ok": True}

    # ---- questions -----------------------------------------------------------------
    @app.post("/api/ask")
    def ask(body: AskBody, request: Request, user=Depends(current_user)):
        visitor, who = requester(request, user)
        p = get_pipeline()
        uid = user["user_id"] if user else None
        req = AskRequest(question=body.question, mode=body.mode, recency=body.recency,
                         role_weight=body.role_weight, scope=Scope(**body.scope.model_dump()), plan=body.plan,
                         history=body.history_dicts(), compare=body.compare)
        model_on = p.has_model()
        key = None
        if model_on and cache_hours > 0:
            last = state.runs(1)
            key = cache_key({"q": " ".join(body.question.lower().split()), **body.model_dump(exclude={"question"}),
                             "model": settings.answer.get("model"), "data": last[0]["run_id"] if last else None,
                             "instructions": INSTRUCTIONS_VERSION})
        count_question(state, body.question)
        record_event(state, "ask", visitor)
        if body.history:
            record_event(state, "follow_up", visitor)
        hit = cached_events(state, key, cache_hours) if key and not body.sources_only else None
        blocked = (check_caps(state, uid, settings.usage, visitor)
                   if model_on and hit is None and not body.sources_only else None)

        def events() -> Iterator[dict[str, Any]]:
            if hit is not None:
                if hit and hit[-1].get("interests"):  # saved before posts in government were left out
                    hit[-1] = {**hit[-1], "interests": without_public_office(hit[-1]["interests"])}
                final = hit[-1]
                if final.get("answer") and not final["answer"].get("no_records") and not final.get("briefing_id"):
                    # cached before shareable links existed: give it an address now, and remember it
                    hit[-1] = {**final, **keep_briefing(body, hit, final)}
                    store_events(state, key, hit, cache_hours)
                yield from hit
                return
            if body.sources_only:
                yield from sources_only_events(body, "Sources only: no briefing was written. Ask again without "
                                                     "'Sources only' for a written briefing.", uid, visitor)
                return
            if blocked:  # the sources still help, and cost nothing
                yield from sources_only_events(body, f"{blocked} The sources found are shown below.", uid, visitor)
                return
            kept: list[dict[str, Any]] = []
            text: list[str] = []
            remind = 0  # > 0: show this reader the note on costs, with the number of briefings so far today
            try:
                for ev in p.run(req):
                    if ev["type"] == "plan" and ev.get("parse_error"):
                        # the model could not read the question (a bad API key, say); offline rules were used
                        record_failure(state, "question reading", body.question, ev.pop("parse_error"))
                    if ev["type"] == "error":
                        record_failure(state, "briefing", body.question, ev.get("message", ""))
                    if ev["type"] == "plan" and ev.get("usage"):
                        record_usage(state, uid, "parse", parse_model(settings), ev["usage"], settings.usage,
                                     visitor)
                    if ev["type"] == "final" and ev.get("interests_usage"):
                        record_usage(state, uid, "interests", parse_model(settings), ev["interests_usage"],
                                     settings.usage, visitor)
                    if ev["type"] == "final":
                        ev = {k: v for k, v in ev.items() if k != "interests_usage"}
                    if ev["type"] == "final" and ev.get("usage"):
                        record_usage(state, uid, "answer", ev.get("model"), ev["usage"], settings.usage, visitor)
                        if uid is None and donate_url(settings):
                            remind = support_reminder_due(state, visitor,
                                                          int(settings.usage.get("support_reminder_every", 10)))
                    if ev["type"] == "final" and key:
                        ev = {**ev, "briefing_key": key}  # lets the reader's feedback refer to this briefing
                    if ev["type"] == "final" and ev.get("answer") and not ev["answer"].get("no_records"):
                        ev = {**ev, **keep_briefing(body, kept, ev)}  # a permanent, shareable address
                    if ev["type"] == "text":
                        text.append(ev["text"])
                    else:
                        kept.append(ev)
                    # the reminder goes to this reader only: never into saved, shared or cached copies
                    yield {**ev, "support_reminder": remind} if ev["type"] == "final" and remind else ev
            except Exception as exc:  # never leave the client hanging
                log.exception("ask failed")
                record_failure(state, "briefing", body.question, f"{type(exc).__name__}: {exc}")
                yield {"type": "error", "message": "The briefing could not be written just now. The sources found are "
                                                   "shown above; please try again later."}
                return
            final = kept[-1] if kept else {}
            if key and final.get("type") == "final" and final.get("answer"):
                replay = [e for e in kept if e["type"] in ("plan", "sources")]
                replay += [{"type": "text", "text": "".join(text)}, {**final, "usage": None, "cached": True}]
                store_events(state, key, replay, cache_hours)

        def guarded() -> Iterator[dict[str, Any]]:
            """A briefing about to be written is reserved at its estimated cost while it runs, and the caps are
            checked again counting the others in progress, so many requests at once cannot all slip past them."""
            if hit is not None or body.sources_only or blocked or not model_on:
                yield from events()
                return
            refused = inflight.start(who)
            if refused:
                yield from sources_only_events(body, f"{refused} The sources found are shown below.", uid, visitor)
                return
            try:
                late = check_caps(state, uid, settings.usage, visitor, inflight.pending_usd(besides=1))
                if late:
                    yield from sources_only_events(body, f"{late} The sources found are shown below.", uid, visitor)
                    return
                yield from events()
            finally:
                inflight.finish(who)

        return StreamingResponse(sse(guarded()), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    def retrieve(body: AskBody, read_with_model: bool = False, uid: int | None = None,
                 visitor: str | None = None) -> dict[str, Any]:
        """The plan and ranked sources for the question or edited plan, without writing a briefing.

        The question is read by the model when ``read_with_model`` (a fraction of a penny with a small model, and far
        more accurate), otherwise by the free built-in rules.
        """
        p = get_pipeline()
        if body.plan:
            plan = QueryPlan.from_dict({"question": body.question, **body.plan})
        elif read_with_model and p.has_model():
            plan, usage = p.make_plan(AskRequest(question=body.question, history=body.history_dicts(),
                                                 compare=body.compare))
            if plan.parse_error:
                record_failure(state, "question reading", body.question, plan.parse_error)
            if usage:
                record_usage(state, uid, "parse", parse_model(settings), usage, settings.usage, visitor)
        else:
            from hansard.rag.query_parse import parse_offline

            plan = parse_offline(body.question, settings.regions, p.today)
            p._set_comparison(plan, AskRequest(question=body.question, compare=body.compare))
        req = AskRequest(question=body.question, mode=body.mode, recency=body.recency, role_weight=body.role_weight,
                         scope=Scope(**body.scope.model_dump()))
        res, _, sources = p.find(plan, req)
        return {"plan": plan.to_dict(), "sources": [s.card() for s in sources if s.kind in ("record", "edm", "devolved", "evidence")],
                "candidates": res.candidates, "semantic": res.semantic_available,
                "evidence": {**res.evidence, "devolution": notes_for(plan.question),
                             "edms": sum(1 for s in sources if s.kind == "edm"),
                             "devolved": sum(1 for s in sources if s.kind == "devolved"),
                             "committee_evidence": sum(1 for s in sources if s.kind == "evidence")}
                if res.evidence else res.evidence}

    def sources_only_events(body: AskBody, note: str, uid: int | None = None,
                            visitor: str | None = None) -> Iterator[dict[str, Any]]:
        # read the question with the model unless the site's own budget is used up
        r = retrieve(body, check_caps(state, None, settings.usage, None) is None, uid, visitor)
        yield {"type": "plan", "plan": r["plan"], "usage": None}
        yield {"type": "sources", **{k: r[k] for k in ("sources", "candidates", "semantic", "evidence")}}
        yield {"type": "final", "answer": None, "usage": None, "note": note}

    @app.post("/api/search")
    def search(body: AskBody, request: Request, user=Depends(current_user)):
        """Retrieval only (no model call): ranked sources for the question or edited plan."""
        requester(request, user)
        return retrieve(body)

    def keep_briefing(body: AskBody, kept: list[dict[str, Any]], final: dict[str, Any]) -> dict[str, Any]:
        return keep(state, body.question, body.model_dump(exclude={"question", "history"}), kept, final)

    # ---- shared briefings ---------------------------------------------------------------
    def _briefing_or_error(bid: str) -> dict[str, Any]:
        b = get_briefing(state, bid)
        if b is None:
            raise HTTPException(404, "There is no briefing at this address.")
        if b["withdrawn_at"]:
            raise HTTPException(410, "This briefing has been withdrawn.")
        return b

    @app.get("/api/briefings/{bid}")
    def briefing(bid: str):
        return public_view(_briefing_or_error(bid))

    @app.get("/b/{bid}/card.png", include_in_schema=False)
    def briefing_card(bid: str):
        b = _briefing_or_error(bid)
        view = public_view(b)
        path = card_png(settings.data_dir / "cards", bid, b["question"], fmt_date(b["created_at"]),
                        fmt_date(b["records_up_to"]), view["evidence"])
        return FileResponse(path, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})

    @app.get("/b/{bid}", include_in_schema=False)
    def briefing_page(bid: str, request: Request):
        """The app, with this briefing's title and link-preview card in the page head."""
        index = dist / "index.html"
        page = index.read_text() if index.is_file() else "<!doctype html><html><head></head><body></body></html>"
        b = get_briefing(state, bid) if ID_PATTERN.match(bid) else None
        base = str(request.base_url).rstrip("/")
        if b is None or b["withdrawn_at"]:
            gone = b is not None
            html_page = page_with_meta(
                page, title="Briefing withdrawn — Where They Stand" if gone else "Briefing not found — Where They Stand",
                description="This briefing is no longer available." if gone else "There is no briefing at this address.",
                url=f"{base}/b/{bid}", image=None)
            return Response(html_page, status_code=410 if gone else 404, media_type="text/html")
        view = public_view(b)
        description = summary_text(view["answer"]) or "A sourced briefing on the record of Parliament."
        html_page = page_with_meta(
            page, title=f"{b['question']} — Where They Stand", description=description, url=f"{base}/b/{bid}",
            image=f"{base}/og-image.png", image_alt="Where They Stand: sourced briefings on the record of Parliament",
            noindex=not is_featured(state, bid))  # featured briefings are for search engines to find
        return Response(with_text(html_page, readable(lambda: briefing_text(view))), media_type="text/html")

    @app.post("/api/admin/briefings/{bid}/withdraw")
    def withdraw(bid: str, body: WithdrawBody, user=Depends(admin_user)):
        if not withdraw_briefing(state, bid, body.reason):
            raise HTTPException(404, "There is no briefing at this address.")
        (settings.data_dir / "cards" / f"{bid}.png").unlink(missing_ok=True)
        return {"ok": True}

    @app.get("/api/admin/briefings")
    def briefings_list(limit: int = 50, user=Depends(admin_user)):
        return {"briefings": recent_briefings(state, min(max(limit, 1), 500))}

    # ---- topic briefings and featured briefings ------------------------------------------------
    def _topic(slug: str):
        from hansard.topics import load_topics, topics_file

        t = next((t for t in load_topics(topics_file(settings)) if t.slug == slug), None)
        if t is None:
            raise HTTPException(404, "There is no topic at this address.")
        return t

    @app.get("/api/topics")
    def topics_index():
        from hansard.topics import topic_listing

        topics = [{k: v for k, v in t.items() if k != "last_check"} for t in topic_listing(settings, state)]
        return {"topics": topics, "featured": featured_list(state)}

    @app.get("/api/topics/{slug}")
    def topic_detail(slug: str):
        from hansard.topics import versions

        t = _topic(slug)
        vs = versions(state, slug)
        b = get_briefing(state, vs[0]["briefing_id"]) if vs else None
        return {"topic": {"slug": t.slug, "title": t.title, "area": t.area, "question": t.question,
                          "description": t.description, "refresh_days": t.refresh_days},
                "briefing": public_view(b) if b else None, "versions": vs}

    @app.get("/topics/{slug}", include_in_schema=False)
    def topic_page(slug: str, request: Request):
        """The app, showing the topic's current briefing, with its title and preview card in the page head."""
        from hansard.topics import current

        t = _topic(slug)
        index = dist / "index.html"
        page = index.read_text() if index.is_file() else "<!doctype html><html><head></head><body></body></html>"
        base = str(request.base_url).rstrip("/")
        cur = current(state, slug)
        b = get_briefing(state, cur["briefing_id"]) if cur else None
        description = (summary_text(public_view(b)["answer"]) if b else "") or t.description or t.question
        html_page = page_with_meta(
            page, title=f"{t.title}: where MPs and peers stand — Where They Stand", description=description,
            url=f"{base}/topics/{slug}", image=f"{base}/og-image.png",
            image_alt="Where They Stand: sourced briefings on the record of Parliament", noindex=False)
        text = readable(lambda: briefing_text(public_view(b), heading=t.title)) if b else None
        return Response(with_text(html_page, text), media_type="text/html")

    @app.get("/sitemap-briefings.xml", include_in_schema=False)
    def sitemap_briefings(request: Request):
        from hansard.topics import topic_listing

        base = str(request.base_url).rstrip("/")
        urls = [f"<url><loc>{base}/topics</loc><changefreq>weekly</changefreq></url>"]
        for t in topic_listing(settings, state):
            if t["current"]:
                urls.append(f"<url><loc>{base}/topics/{t['slug']}</loc>"
                            f"<lastmod>{t['current']['prepared_at'][:10]}</lastmod></url>")
        urls += [f"<url><loc>{base}/b/{f['id']}</loc><lastmod>{f['prepared_at'][:10]}</lastmod></url>"
                 for f in featured_list(state)]
        return Response('<?xml version="1.0" encoding="UTF-8"?><urlset '
                        f'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{"".join(urls)}</urlset>',
                        media_type="application/xml")

    @app.get("/api/admin/topics")
    def admin_topics(user=Depends(admin_user)):
        from hansard.topics import topic_listing

        return {"topics": topic_listing(settings, state), "featured": featured_list(state)}

    @app.post("/api/admin/featured")
    def admin_feature(body: FeatureBody, user=Depends(admin_user)):
        m = re.search(r"([a-km-z2-9]{10})/?$", body.briefing.strip())
        if not m or not feature(state, m.group(1), body.title, body.blurb):
            raise HTTPException(404, "There is no briefing at that address (or it has been withdrawn).")
        return {"ok": True, "id": m.group(1)}

    @app.delete("/api/admin/featured/{bid}")
    def admin_unfeature(bid: str, user=Depends(admin_user)):
        if not unfeature(state, bid):
            raise HTTPException(404, "That briefing is not featured.")
        return {"ok": True}

    # ---- bills ---------------------------------------------------------------------------------
    def get_bills():
        from hansard.bills import Bills

        with lock:
            if "bills" not in holder:
                holder["bills"] = Bills(settings)
        return holder["bills"]

    @app.get("/api/bills")
    def bills_index():
        """Every bill debated since 2010, newest first (summary only)."""
        out = []
        for b in get_bills().all(state.latest_record_date()):
            last = b["sittings"][-1]
            out.append({k: b[k] for k in ("slug", "name", "first", "last", "houses", "contributions")}
                       | {"sittings": len(b["sittings"]), "divisions": len(b["divisions"]),
                          "latest": f"{', '.join(last['stages'])} ({'Lords' if last['house'] == 'lords' else 'Commons'})"})
        return {"bills": out}

    @app.get("/api/bills/{slug}")
    def bill_detail(slug: str):
        b = get_bills().get(slug, state.latest_record_date())
        if b is None:
            raise HTTPException(404, "There is no bill at this address.")
        return b

    @app.get("/bills/{slug}", include_in_schema=False)
    def bill_page(slug: str, request: Request):
        b = get_bills().get(slug, state.latest_record_date())
        index = dist / "bill.html"
        page = index.read_text() if index.is_file() else "<!doctype html><html><head></head><body></body></html>"
        base = str(request.base_url).rstrip("/")
        if b is None:
            return Response(page_with_meta(page, title="Bill not found — Where They Stand",
                                           description="There is no bill at this address.", url=f"{base}/bills/{slug}",
                                           image=None), status_code=404, media_type="text/html")
        houses = " and ".join("the Lords" if h == "lords" else "the Commons" for h in b["houses"])
        description = (f"The {b['name']} in Parliament: every debate and vote in {houses}, "
                       f"{fmt_date(b['first'])} to {fmt_date(b['last'])}, from the official record.")
        return Response(with_text(page_with_meta(page, title=f"{b['name']} — Where They Stand", description=description,
                                                 url=f"{base}/bills/{slug}", image=None, noindex=False),
                                  readable(lambda: bill_text(b))), media_type="text/html")

    @app.get("/sitemap-bills.xml", include_in_schema=False)
    def sitemap_bills(request: Request):
        base = str(request.base_url).rstrip("/")
        urls = "".join(f"<url><loc>{base}/bills/{b['slug']}</loc><lastmod>{b['last']}</lastmod></url>"
                       for b in get_bills().all(state.latest_record_date()))
        return Response('<?xml version="1.0" encoding="UTF-8"?><urlset '
                        f'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>',
                        media_type="application/xml")

    # ---- debates -------------------------------------------------------------------------------
    def get_debates():
        from hansard.debates import Debates

        with lock:
            if "debates" not in holder:
                holder["debates"] = Debates(settings)
        return holder["debates"]

    _DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    _SLUG = re.compile(r"^[a-z0-9-]{1,200}$")

    def find_debate(day: str, chamber: str, slug: str):
        if not (_DAY.match(day) and _SLUG.match(chamber) and _SLUG.match(slug)):
            return None
        return get_debates().get(day, chamber, slug, state.latest_record_date())

    @app.get("/api/debates/{day}/{chamber}/{slug}")
    def debate_detail(day: str, chamber: str, slug: str):
        """A whole debate as recorded, in order, with each speaker as they were on the day. Free: no AI."""
        d = find_debate(day, chamber, slug)
        if d is None:
            raise HTTPException(404, "There is no debate at this address.")
        return d

    @app.get("/debates/{day}/{chamber}/{slug}", include_in_schema=False)
    def debate_page(day: str, chamber: str, slug: str, request: Request):
        d = find_debate(day, chamber, slug)
        index = dist / "debate.html"
        page = index.read_text() if index.is_file() else "<!doctype html><html><head></head><body></body></html>"
        url = f"{str(request.base_url).rstrip('/')}/debates/{day}/{chamber}/{slug}"
        if d is None:
            return Response(page_with_meta(page, title="Debate not found — Where They Stand",
                                           description="There is no debate at this address.", url=url, image=None),
                            status_code=404, media_type="text/html")
        if d.get("kind") == "statement":
            description = (f"{d['title']}: a written statement to the {d['chamber_name']} on {fmt_date(d['date'])}"
                           + (f" ({d['department']})" if d.get("department") else "") + ", as published by Parliament.")
        else:
            description = (f"{d['title']}: the debate in the {d['chamber_name']} on {fmt_date(d['date'])}, as "
                           f"recorded in Hansard, with {d['contributions']} contributions from {d['speakers']} members.")
        return Response(with_text(page_with_meta(page, title=f"{d['title']}, {fmt_date(d['date'])} — Where They Stand",
                                                 description=description, url=url, image=None, noindex=False),
                                  readable(lambda: debate_text(d))), media_type="text/html")

    # ---- the week in Parliament ---------------------------------------------------------------
    def get_weeks():
        from hansard.week import Weeks

        with lock:
            if "weeks" not in holder:
                holder["weeks"] = Weeks(settings)
        return holder["weeks"]

    def find_week(start: str | None):
        from hansard.week import monday

        w = get_weeks()
        if start is None:
            day = w.latest()
        else:
            try:
                day = date.fromisoformat(start) if _DAY.match(start) else None
            except ValueError:
                day = None
        return w.get(monday(day), state.latest_record_date()) if day else None

    @app.get("/api/week")
    def week_detail(start: str | None = None):
        """One sitting week (Monday to Sunday; the latest if no start is given). Free: no AI."""
        return remembered(f"week:{start}", lambda: week_data(start))

    def week_data(start: str | None):
        w = find_week(start)
        if w is None:
            raise HTTPException(404, "Parliament did not sit that week, or the week is outside the record.")
        if start is None:  # "this week": say so when Parliament has not sat lately (a recess)
            return {**w, "pause": get_weeks().pause(date.today())}
        return w

    def week_page(start: str | None, request: Request) -> Response:
        w = find_week(start)
        index = dist / "week.html"
        page = index.read_text() if index.is_file() else "<!doctype html><html><head></head><body></body></html>"
        base = str(request.base_url).rstrip("/")
        if w is None:
            return Response(page_with_meta(page, title="Week not found — Where They Stand",
                                           description="Parliament did not sit that week.",
                                           url=f"{base}/weeks/{start}", image=None),
                            status_code=404, media_type="text/html")
        url = f"{base}/this-week" if start is None else f"{base}/weeks/{w['start']}"
        span = f"{fmt_date(w['start'])} to {fmt_date(w['end'])}"
        return Response(with_text(page_with_meta(
            page, title=f"In Parliament, week of {fmt_date(w['start'])} — Where They Stand",
            description=f"The debates, votes, bills and statements in Parliament, {span}, from the official record.",
            url=url, image=None, noindex=False), readable(lambda: week_text(w))), media_type="text/html")

    @app.get("/this-week", include_in_schema=False)
    def this_week_page(request: Request):
        return week_page(None, request)

    @app.get("/weeks/{start}", include_in_schema=False)
    def a_week_page(start: str, request: Request):
        return week_page(start, request)

    # ---- early day motions -----------------------------------------------------------------------
    # ---- the public search API (no AI) -----------------------------------------------------------
    @app.get("/api/v1/search")
    def public_search(request: Request, q: str = "", date_from: str | None = None, date_to: str | None = None,
                      house: str | None = None, party: str | None = None, written: bool = True,
                      limit: int = 20, user=Depends(current_user)):
        """Search the Westminster record since 1997, ranked by relevance. Free; no AI. See /developers."""
        from hansard.engine import Filters

        requester(request, user)
        q = " ".join(q.split())[:300]
        if len(q) < 2:
            raise HTTPException(400, "Give some words to search for (q).")
        try:
            filters = Filters(date_from=date.fromisoformat(date_from) if date_from else None,
                              date_to=date.fromisoformat(date_to) if date_to else None,
                              houses=[house] if house in ("commons", "lords") else [],
                              parties=[party] if party else [])
        except ValueError as exc:
            raise HTTPException(400, "Dates must be written as YYYY-MM-DD.") from exc
        res = get_pipeline().engine.search(q, filters, Scope(written=written), [], 0.0, 1.0,
                                           max(1, min(int(limit), 50)), diversify=False)
        out = []
        for r in res.records:
            sp = r.get("speaker") or {}
            out.append({"record_id": r["record_id"], "date": r["date"], "house": r["house"], "chamber": r["chamber"],
                        "type": r["record_type"], "heading": r.get("heading"),
                        "speaker": {"name": sp.get("name") or r.get("speaker_name_raw"), "party": sp.get("party"),
                                    "constituency": sp.get("constituency"), "person_id": sp.get("person_id")},
                        "excerpt": " ".join((r.get("excerpt") or "").split())[:1000],
                        "column": r.get("colnum"), "uin": r.get("uin"),
                        "url": r.get("official_url") or r.get("twfy_url"), "twfy_url": r.get("twfy_url"),
                        "relevance": round(r["ranking"]["relevance"], 4) if r.get("ranking") else None})
        body = {"query": q, "results": out, "count": len(out),
                "licence": "Contains Parliamentary information licensed under the Open Parliament Licence v3.0."}
        return JSONResponse(body, headers={"Server-Timing": server_timing(res.timings)})

    # ---- the devolved parliaments --------------------------------------------------------------
    def get_devolved():
        from hansard.devolved import DevolvedStore, Members
        from hansard.sync import devolved_path

        with lock:
            if "devolved" not in holder:
                holder["devolved"] = (DevolvedStore(devolved_path(settings)),
                                      Members(settings.parlparse_dir / "members" / "people.json"))
        return holder["devolved"]

    def devolved_speaker(members, pp, leg: str, row: dict) -> dict:
        from hansard.devolved import LEGISLATURES

        on = members.on(row.get("person_id"), LEGISLATURES[leg]["org"], row["date"])
        raw = re.sub(r"^\d+\.\s*", "", row.get("speaker") or "")
        number = None
        if row.get("person_id"):
            from hansard.people import person_number

            number = person_number(row["person_id"])
        name = on.get("name") or raw or None
        role = raw.split("(")[0].strip() if raw.lower().startswith(("the ", "deputy ")) else None
        return {"name": name, "party": on.get("party"), "seat": on.get("seat"), "role": role, "number": number,
                "slug": re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-") if number else None}

    @app.get("/api/devolved")
    def devolved_index():
        """The devolved parliaments' recent sitting days. Free: no AI."""
        from hansard.devolved import LEGISLATURES

        store, _ = get_devolved()
        counts = store.counts()
        return {"legislatures": [{"id": k, "name": v["name"], "member": v["member"], "nation": v["nation"],
                                  **(counts.get(k) or {"speeches": 0}), "days": store.days(k, 12)}
                                 for k, v in LEGISLATURES.items()]}

    @app.get("/api/devolved/search")
    def devolved_search(q: str = "", leg: str | None = None, page: int = 1):
        from hansard.devolved import LEGISLATURES

        if leg and leg not in LEGISLATURES:
            raise HTTPException(404, "Unknown parliament.")
        store, members = get_devolved()
        page = max(1, min(page, 200))
        total, rows = store.search(q[:200], leg, limit=25, offset=(page - 1) * 25)
        pp = get_people()
        return {"total": total, "page": page, "per_page": 25,
                "items": [{**r, "speaker": devolved_speaker(members, pp, r["legislature"], r),
                           "body": LEGISLATURES[r["legislature"]]["name"]} for r in rows]}

    @app.get("/api/devolved/{leg}")
    def devolved_days(leg: str, before: str | None = None):
        from hansard.devolved import LEGISLATURES

        if leg not in LEGISLATURES or (before and not _DAY.match(before)):
            raise HTTPException(404, "Unknown parliament.")
        store, _ = get_devolved()
        return {"id": leg, **LEGISLATURES[leg], "days": store.days(leg, 40, before)}

    @app.get("/api/devolved/{leg}/{key}")
    def devolved_day(leg: str, key: str):
        from hansard.devolved import LEGISLATURES

        if leg not in LEGISLATURES or not _SLUG.match(key.replace("_", "-")):
            raise HTTPException(404, "Unknown parliament.")
        store, members = get_devolved()
        rows = store.day(leg, key)
        if not rows:
            raise HTTPException(404, "There is no sitting at this address.")
        pp = get_people()
        items, section = [], None
        for r in rows:
            head = r["major"] or ""
            if head and head != section:
                section = head
                items.append({"kind": "heading", "text": head, "id": f"h-{r['ord']}"})
            items.append({"kind": "said" if r["person_id"] else "procedural", "id": f"s{r['ord']}",
                          "minor": r["minor"], "time": r["time"], "url": r["url"],
                          "speaker": devolved_speaker(members, pp, leg, r) if r["person_id"] else None,
                          "paragraphs": [p for p in r["text"].split("\n\n") if p]})
        return {"id": leg, "body": LEGISLATURES[leg]["name"], "member": LEGISLATURES[leg]["member"],
                "key": key, "date": rows[0]["date"], "items": items,
                "speakers": len({r["person_id"] for r in rows if r["person_id"]}),
                "contributions": sum(1 for r in rows if r["person_id"]),
                "official_url": next((r["url"] for r in rows if r["url"]), None)}

    @app.get("/devolved/{leg}/{key}", include_in_schema=False)
    def devolved_day_page(leg: str, key: str, request: Request):
        from hansard.devolved import LEGISLATURES

        index = dist / "devolvedday.html"
        page = index.read_text() if index.is_file() else "<!doctype html><html><head></head><body></body></html>"
        base = str(request.base_url).rstrip("/")
        rows = get_devolved()[0].day(leg, key) if leg in LEGISLATURES else []
        if not rows:
            return Response(page_with_meta(page, title="Not found — Where They Stand", description="Nothing here.",
                                           url=f"{base}/devolved/{leg}/{key}", image=None),
                            status_code=404, media_type="text/html")
        name = LEGISLATURES[leg]["name"]
        return Response(with_text(page_with_meta(page, title=f"{name}, {fmt_date(rows[0]['date'])} — Where They Stand",
                                                 description=f"What was said in the {name} on {fmt_date(rows[0]['date'])}, "
                                                             "as recorded in its official report, with each member's "
                                                             "party and seat.",
                                                 url=f"{base}/devolved/{leg}/{key}", image=None, noindex=False),
                                  readable(lambda: devolved_day_text(devolved_day(leg, key)))),
                        media_type="text/html")

    def get_committees():
        from hansard.sources.committees import Committees
        from hansard.sync import committees_path

        with lock:
            if "committees" not in holder:
                holder["committees"] = Committees(committees_path(settings))
        c = holder["committees"]
        if not c.available():
            raise HTTPException(503, "Committee information is being collected and will appear after the next update.")
        return c

    @app.get("/api/committees")
    def committees_index():
        """Current select and other committees of both Houses. Free: no AI."""
        c = get_committees()
        return {"committees": c.index(), "totals": c.stats()}

    @app.get("/api/committees/{cid}")
    def committee_detail(cid: int):
        d = get_committees().committee(cid)
        if d is None:
            raise HTTPException(404, "There is no committee with that number.")
        return d

    @app.get("/api/inquiries/{iid}")
    def inquiry_detail(iid: int):
        d = get_committees().inquiry(iid)
        if d is None:
            raise HTTPException(404, "There is no inquiry with that number.")
        have = get_evidence().available()
        for s in d["sessions"]:
            s["transcript"] = s["id"] in have
        return d

    def get_evidence():
        from hansard.sources.evidence import EvidenceStore
        from hansard.sync import evidence_path

        with lock:
            if "evidence" not in holder:
                holder["evidence"] = EvidenceStore(evidence_path(settings))
        return holder["evidence"]

    @app.get("/api/evidence")
    def evidence_search(q: str = "", page: int = 1):
        """What witnesses and committee members said in oral evidence (transcripts collected so far)."""
        page = max(1, min(page, 200))
        total, rows = get_evidence().search(q[:200], limit=25, offset=(page - 1) * 25)
        sessions = {r["session_id"] for r in rows}
        meta = get_committees().sessions_meta(sessions) if sessions else {}
        return {"total": total, "page": page, "per_page": 25,
                "items": [{**r, **meta.get(r["session_id"], {})} for r in rows]}

    @app.get("/api/evidence/{sid}")
    def evidence_detail(sid: int):
        t = get_evidence().transcript(sid)
        if t is None:
            raise HTTPException(404, "There is no transcript for that session here yet.")
        return {**t, **get_committees().sessions_meta({sid}).get(sid, {})}

    @app.get("/evidence/{sid}", include_in_schema=False)
    def evidence_page(sid: int, request: Request):
        index = dist / "evidence.html"
        page = index.read_text() if index.is_file() else "<!doctype html><html><head></head><body></body></html>"
        url = f"{str(request.base_url).rstrip('/')}/evidence/{sid}"
        t = get_evidence().transcript(sid)
        if t is None:
            return Response(page_with_meta(page, title="Transcript not found — Where They Stand",
                                           description="There is no transcript for that session here yet.", url=url,
                                           image=None), status_code=404, media_type="text/html")
        title = t["title"] or "Oral evidence"
        return Response(with_text(page_with_meta(page, title=f"{title} — Where They Stand",
                                                 description=f"{title}: the transcript of a committee evidence session, "
                                                             "with who said what.", url=url, image=None, noindex=False),
                                  readable(lambda: evidence_text({**t, **get_committees().sessions_meta({sid}).get(sid, {})}))),
                        media_type="text/html")

    @app.get("/api/witnesses")
    def witness_search(q: str = ""):
        """Organisations and people who gave oral evidence to committees, and how often."""
        return get_committees().witnesses(q[:200])

    def committee_meta_page(kind: str, ident: int, request: Request) -> Response:
        index = dist / f"{kind}.html"
        page = index.read_text() if index.is_file() else "<!doctype html><html><head></head><body></body></html>"
        base = str(request.base_url).rstrip("/")
        try:
            c = get_committees()
            d = c.committee(ident) if kind == "committee" else c.inquiry(ident)
        except HTTPException:
            d = None
        path = f"/committees/{ident}" if kind == "committee" else f"/inquiries/{ident}"
        if d is None:
            return Response(page_with_meta(page, title="Not found — Where They Stand", description="Nothing here.",
                                           url=base + path, image=None), status_code=404, media_type="text/html")
        if kind == "committee":
            title, desc = d["name"], (d.get("purpose") or f"The {d['name']}: its inquiries, reports and the "
                                      "organisations it has heard from.")
        else:
            title = d["title"]
            desc = (f"{d['title']}: an inquiry by the " + " and ".join(x["name"] for x in d["committees"]) +
                    ", with its reports, the government's responses and who gave evidence.")
        text = committee_text if kind == "committee" else inquiry_text
        return Response(with_text(page_with_meta(page, title=f"{title} — Where They Stand", description=desc[:300],
                                                 url=base + path, image=None, noindex=False), readable(lambda: text(d))),
                        media_type="text/html")

    @app.get("/committees/{cid}", include_in_schema=False)
    def committee_page(cid: int, request: Request):
        return committee_meta_page("committee", cid, request)

    @app.get("/inquiries/{iid}", include_in_schema=False)
    def inquiry_page(iid: int, request: Request):
        return committee_meta_page("inquiry", iid, request)

    def get_register():
        from hansard.sources.regmem import Register
        from hansard.sync import regmem_path

        with lock:
            if "register" not in holder:
                holder["register"] = Register(regmem_path(settings))
        return holder["register"]

    def get_donations():
        from hansard.sources.donations import Donations
        from hansard.sync import donations_path

        with lock:
            if "donations" not in holder:
                holder["donations"] = Donations(donations_path(settings))
        return holder["donations"]

    def get_directorships():
        from hansard.sources.companies import Directorships
        from hansard.sync import directorships_path

        with lock:
            if "directorships" not in holder:
                holder["directorships"] = Directorships(directorships_path(settings))
        return holder["directorships"]

    @app.get("/api/directorships")
    def directorships_index(q: str = ""):
        """Members' current company roles at Companies House, by company; ``q`` searches companies and members."""
        return get_directorships().index(q[:200])

    def get_groups():
        from hansard.sources.appgs import Groups
        from hansard.sync import appgs_path

        with lock:
            if "groups" not in holder:
                holder["groups"] = Groups(appgs_path(settings))
        return holder["groups"]

    lighter_lock = threading.Lock()

    @app.get("/api/lighter-side")
    def lighter_side():
        """What made the House laugh, the same week in earlier years, and early day motions you might not expect:
        from the record by plain rules, no AI. Rebuilt every few hours."""
        def build() -> dict[str, Any]:
            import duckdb

            from hansard.lighter import Lighter, lighter_edms
            from hansard.reference.profiles import attach_reference
            from hansard.store.parquet import attach_store
            from hansard.sync import edms_path

            with lighter_lock:
                con = duckdb.connect()
                try:
                    stores = set(attach_store(con, settings.parquet_dir))
                    attach_reference(con, settings.reference_dir)
                    lighter = Lighter(con)
                    found = ({"laughter": lighter.laughter(), "on_this_day": lighter.on_this_day()}
                             if "records" in stores else {"laughter": [], "on_this_day": []})
                finally:
                    con.close()
            people = get_people()
            for section in found.values():  # each speaker's page and party on the day
                for m in section:
                    who = people.brief(m.pop("person_id"), m["date"]) if m.get("person_id") else None
                    m["member"] = who
            edms = lighter_edms(edms_path(settings))
            names = people.by_parliament_id([(e["mnis"], e["date"]) for e in edms if e.get("mnis")])
            for e in edms:  # the member's name as on their page ("John McDonnell"), not as the motion lists it
                e["tabled_by"] = (names.get((e.pop("mnis"), e["date"])) or {}).get("name") or e["tabled_by"]
            return {**found, "edms": edms, "built": date.today().isoformat()}

        return remembered("lighter-side", build, ttl=6 * 3600)

    @app.get("/api/groups")
    def groups_index(q: str = "", parliament: str = "uk"):
        """All-party parliamentary groups (or a devolved parliament's cross-party groups), and their biggest
        registered funders. Free: no AI."""
        return remembered(f"groups:{parliament}:{q[:200]}", lambda: groups_index_data(q, parliament))

    def groups_index_data(q: str, parliament: str):
        from hansard.sources.appgs import PARLIAMENTS

        if parliament not in PARLIAMENTS:
            raise HTTPException(404, "Unknown parliament.")
        return get_groups().index(q[:200], parliament)

    @app.get("/api/groups/funder")
    def groups_funder(name: str):
        return {"funder": name, "benefits": get_groups().funder(name[:300])}

    @app.get("/api/groups/{parliament}/{slug}")
    def group_detail(parliament: str, slug: str):
        g = get_groups().group(slug, parliament)
        if g is None:
            raise HTTPException(404, "There is no group at this address.")
        people = get_people()
        for m in g["members"]:
            m["member"] = people.brief(m["person_id"], m["updated"]) if m["person_id"] else None
        return g

    @app.get("/groups/{parliament}/{slug}", include_in_schema=False)
    def group_page(parliament: str, slug: str, request: Request):
        index = dist / "group.html"
        page = index.read_text() if index.is_file() else "<!doctype html><html><head></head><body></body></html>"
        g = get_groups().group(slug, parliament)
        base = str(request.base_url).rstrip("/")
        if g is None:
            return Response(page_with_meta(page, title="Group not found — Where They Stand",
                                           description="There is no group at this address.",
                                           url=f"{base}/groups/{parliament}/{slug}", image=None),
                            status_code=404, media_type="text/html")
        desc = (g["purpose"] or g["title"])[:280]
        return Response(with_text(page_with_meta(page, title=f"{g['title']} — Where They Stand", description=desc,
                                                 url=f"{base}/groups/{parliament}/{slug}", image=None, noindex=False),
                                  readable(lambda: group_text(g))),
                        media_type="text/html")

    @app.get("/api/donations")
    def donations_index():
        """The biggest donors to MPs in the last five years and the latest donations, as registered with the
        Electoral Commission and matched to MPs. Free: no AI."""
        return remembered("donations", donations_index_data)

    def donations_index_data():
        out = get_donations().index()
        people = get_people()
        for r in out.get("recent", []):
            r["member"] = people.brief(r["person_id"], r["accepted"]) if r.get("person_id") else None
        return out

    @app.get("/api/donations/donors")
    def donations_donors(q: str = ""):
        return {"query": q, "donors": get_donations().donors(q[:200])}

    @app.get("/api/donations/donor")
    def donations_by_donor(name: str):
        people = get_people()
        rows = get_donations().donor(name[:300])
        for r in rows:
            r["member"] = people.brief(r["person_id"], r["accepted"]) if r.get("person_id") else None
        return {"donor": name, "donations": rows}

    def get_registers():
        from hansard.sources.registers import Registers

        with lock:
            if "registers" not in holder:
                holder["registers"] = Registers(settings.data_dir)
        return holder["registers"]

    def get_edms():
        from hansard.sources.edms import EdmStore
        from hansard.sync import edms_path

        with lock:
            if "edms" not in holder:
                holder["edms"] = EdmStore(edms_path(settings))
        return holder["edms"]

    def edm_member(found: dict, mnis: int | None, on: str | None, name: str | None, party: str | None) -> dict:
        m = found.get((mnis, on)) or {}
        return {"name": m.get("name") or name, "party": m.get("party") or party, "number": m.get("number"),
                "slug": m.get("slug")}

    def edm_rows(rows: list[dict]) -> list[dict]:
        found = get_people().by_parliament_id([(r["primary_mnis"], r["date_tabled"]) for r in rows
                                               if r["primary_mnis"]])
        return [{**{k: r[k] for k in ("id", "uin", "title", "date_tabled", "status", "sponsors_count", "prayer")},
                 "sponsor": edm_member(found, r["primary_mnis"], r["date_tabled"], r["primary_name"],
                                       r["primary_party"])} for r in rows]

    @app.get("/api/edms")
    def edms_index(q: str = "", page: int = 1, person: int | None = None):
        """Early day motions, newest first, optionally matching words or signed by one member. Free: no AI."""
        store = get_edms()
        page = max(1, min(page, 500))
        mnis = None
        if person is not None:
            pp = get_people()
            pid = pp.resolve(person)
            info = pp.page(pid) if pid else None
            if not info or not info.get("parliament_id"):
                raise HTTPException(404, "There is no member with that number.")
            mnis = [int(info["parliament_id"])]
        total, rows = store.search(q[:200], mnis=mnis, limit=25, offset=(page - 1) * 25)
        return {"total": total, "page": page, "per_page": 25, "items": edm_rows(rows), "available": store.count() > 0}

    @app.get("/api/edms/{edm_id}")
    def edm_detail(edm_id: int):
        e = get_edms().get(edm_id)
        if e is None:
            raise HTTPException(404, "There is no early day motion with that number.")
        pairs = [(s["mnis"], s["signed"]) for s in e["sponsors"]]
        if e["primary_mnis"]:
            pairs.append((e["primary_mnis"], e["date_tabled"]))
        found = get_people().by_parliament_id(pairs)
        sponsors = [{**edm_member(found, s["mnis"], s["signed"], s["name"], s["party"]),
                     **{k: s[k] for k in ("sponsoring_order", "signed", "withdrawn")}} for s in e["sponsors"]]
        parties: dict[str, int] = {}
        for s in sponsors:
            if not s["withdrawn"]:
                parties[s["party"] or "Unknown"] = parties.get(s["party"] or "Unknown", 0) + 1
        return {**{k: e[k] for k in ("id", "uin", "title", "text", "date_tabled", "status", "status_date",
                                     "sponsors_count", "amendment_to", "prayer")},
                "sponsor": edm_member(found, e["primary_mnis"], e["date_tabled"], e["primary_name"],
                                      e["primary_party"]),
                "sponsors": sponsors, "signatures_known": bool(e["sponsors_fetched"]),
                "party_counts": sorted(parties.items(), key=lambda x: (-x[1], x[0])),
                "amendments": e["amendments"],
                "official_url": f"https://edm.parliament.uk/early-day-motion/{e['id']}"}

    @app.get("/edms/{edm_id}", include_in_schema=False)
    def edm_page(edm_id: int, request: Request):
        e = get_edms().get(edm_id)
        index = dist / "edm.html"
        page = index.read_text() if index.is_file() else "<!doctype html><html><head></head><body></body></html>"
        url = f"{str(request.base_url).rstrip('/')}/edms/{edm_id}"
        if e is None:
            return Response(page_with_meta(page, title="Early day motion not found — Where They Stand",
                                           description="There is no early day motion with that number.", url=url,
                                           image=None), status_code=404, media_type="text/html")
        when = fmt_date(e["date_tabled"]) if e["date_tabled"] else "an unknown date"
        return Response(with_text(page_with_meta(
            page, title=f"{e['title']} (early day motion) — Where They Stand",
            description=f"Early day motion tabled on {when} by {e['primary_name']}, with "
                        f"{e['sponsors_count'] or 0} signatures: the text and who signed.",
            url=url, image=None, noindex=False), readable(lambda: edm_text(edm_detail(edm_id)))), media_type="text/html")

    # ---- charts --------------------------------------------------------------------------------
    chart_slots = threading.Semaphore(2)  # each chart scans the record for a few seconds; keep the server responsive

    def get_trends():
        from hansard.trends import Trends

        today = get_pipeline().today  # before taking the lock: get_pipeline takes it too
        with lock:
            if "trends" not in holder:
                holder["trends"] = Trends(settings, today)
        return holder["trends"]

    @app.get("/api/trends")
    def trends(request: Request, q: str = "", by: str = "all", user=Depends(current_user)):
        """How often words were used in Parliament each year, with the context to read the numbers (free)."""
        requester(request, user)
        phrases = [p for p in q.split(",") if p.strip()]
        if by == "parliaments":
            return parliaments_chart(phrases[:1])
        if not chart_slots.acquire(timeout=30):
            raise HTTPException(503, "The site is busy drawing other charts. Please try again in a moment.")
        try:
            return get_trends().chart(phrases, by, str(state.latest_record_date()))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        finally:
            chart_slots.release()

    def parliaments_chart(phrases: list[str]) -> dict[str, Any]:
        """Westminster's two Houses and the three devolved parliaments, side by side, as rates (comparable)."""
        from hansard.devolved import LEGISLATURES
        from hansard.trends import clean_phrase

        try:
            phrase = clean_phrase(phrases[0]) if phrases else ""
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if not phrase:
            raise HTTPException(400, "Give a word or phrase.")
        if not chart_slots.acquire(timeout=30):
            raise HTTPException(503, "The site is busy drawing other charts. Please try again in a moment.")
        try:
            base = get_trends().chart([phrase], "house", str(state.latest_record_date()))
        finally:
            chart_slots.release()
        dev = get_devolved()[0].yearly(phrase)
        first = min((min(v) for v in dev.values() if v), default=None)
        years = [y for y in base["years"] if first is None or y >= first]
        keep = [base["years"].index(y) for y in years]
        series = [{**s, **{k: [s[k][i] for i in keep] for k in ("counts", "totals", "rate")}} for s in base["series"]]
        for leg, cfg in LEGISLATURES.items():
            by_year = dev.get(leg) or {}
            if not by_year:
                continue
            counts = [by_year[y][0] if y in by_year else None for y in years]
            totals = [by_year[y][1] if y in by_year else None for y in years]
            series.append({"key": f"{phrase}|{leg}", "phrase": phrase, "group": leg, "label": cfg["name"],
                           "counts": counts, "totals": totals,
                           "rate": [round(10000 * c / t, 2) if c is not None and t else None
                                    for c, t in zip(counts, totals, strict=True)]})
        partial = sorted({*[y for y in base["partial_years"] if y in years], *([first] if first else [])})
        return {"phrases": [phrase], "by": "parliaments", "years": years, "series": series,
                "partial_years": partial, "total": sum(sum(c or 0 for c in s["counts"]) for s in series)}

    # ---- timelines -----------------------------------------------------------------------------
    @app.get("/api/timeline")
    def timeline_api(request: Request, q: str = "", person: int | None = None, party: str | None = None,
                     user=Depends(current_user)):
        """What a member or a party said on a topic, and how they voted, in date order (free: no AI)."""
        from hansard.timeline import timeline

        requester(request, user)
        q = " ".join(q.split())
        if len(q) < 3:
            raise HTTPException(400, "Give a topic of at least three letters.")
        pid = get_people().resolve(person) if person is not None else None
        if person is not None and pid is None:
            raise HTTPException(404, "There is no member with that number.")
        if (pid is None) == (not party):
            raise HTTPException(400, "Choose a member or a party.")
        if party and party not in {p for p, _ in get_pipeline().parties()}:
            raise HTTPException(404, "There is no party with that name.")
        return timeline(get_pipeline().engine, get_people(), q, person_id=pid, party_id=party)

    # ---- MPs and peers -----------------------------------------------------------------------
    @app.get("/api/people")
    def people_list(q: str = "", everyone: bool = False):
        return {"people": get_people().search(q.strip()[:80], current_only=not everyone)}

    @app.get("/api/people/{number}")
    def person(number: int, part: Literal["all", "core", "activity", "interests"] = "all"):
        """A member's page. The page asks for it in three parts so the first shows at once: ``core`` (who they are,
        seats, roles, membership), ``activity`` (contributions, votes, motions, devolved and committee evidence)
        and ``interests`` (registers, donations, company roles, groups). Without ``part``, everything."""
        if part == "all":
            return remembered(f"person:{number}", lambda: {**person_core(number), **person_activity(number),
                                                            **person_interests(number)})
        fn = {"core": person_core, "activity": person_activity, "interests": person_interests}[part]
        return remembered(f"person:{number}:{part}", lambda: fn(number))

    @app.get("/api/people/{number}/contributions")
    def person_contributions(number: int, offset: int = Query(0, ge=0, le=2000), limit: int = Query(10, ge=1, le=50)):
        """More of a member's contributions, newest first (substantive ones: not "Will the member give way?")."""
        pp = get_people()
        pid = pp.resolve(number)
        if not pid:
            raise HTTPException(404, "There is no member with that number.")
        rows = remembered(f"person:{number}:contrib:{offset + limit + 1}",
                          lambda: pp.contributions(pid, limit=offset + limit + 1))
        return {"contributions": rows[offset:offset + limit], "more": len(rows) > offset + limit}

    def person_core(number: int) -> dict[str, Any]:
        pp = get_people()
        pid = pp.resolve(number)
        page = pp.page(pid) if pid else None
        if page is None:
            raise HTTPException(404, "There is no member with that number.")
        try:
            store, members = get_devolved()
            ms = members.memberships(pid)
            now = [m for m in ms if m["end"] is None]
            if now and not page.get("current"):  # a sitting MSP, MS or MLA (and not an MP or peer)
                m = now[-1]
                page = {**page, "status": f"{m['member']} for {m['seat']} ({m['body']})"
                                          + (f", {m['party']}" if m["party"] else "")}
        except (OSError, ValueError):
            pass
        return page

    def person_activity(number: int) -> dict[str, Any]:
        pp = get_people()
        pid = pp.resolve(number)
        if not pid:
            raise HTTPException(404, "There is no member with that number.")
        page = remembered(f"person:{number}:core", lambda: person_core(number))
        edms = None
        if page.get("parliament_id"):
            try:
                edms = get_edms().member_summary(int(page["parliament_id"]), recent=8)
                edms["recent"] = edm_rows(edms["recent"])
            except (ValueError, OSError):
                edms = None
        evidence = []
        if page.get("parliament_id"):
            try:
                evidence = get_committees().for_member(int(page["parliament_id"]))
            except (HTTPException, ValueError, OSError):
                evidence = []
        devolved = None
        try:
            store, members = get_devolved()
            ms = members.memberships(pid)
            if ms:
                devolved = {"memberships": ms, **store.for_person(pid)}
        except (OSError, ValueError):
            devolved = None
        contributions = pp.contributions(pid, limit=11)
        return {"contributions": contributions[:10], "contributions_more": len(contributions) > 10,
                "voting": pp.voting(pid), "edms": edms, "devolved": devolved, "committee_evidence": evidence,
                "topics": pp.topics(pid)}

    def person_interests(number: int) -> dict[str, Any]:
        from hansard.sources.companies import mark_declared as _mark_declared

        pp = get_people()
        pid = pp.resolve(number)
        if not pid:
            raise HTTPException(404, "There is no member with that number.")
        interests, registers = get_register().for_person(pid), get_registers().for_person(pid)
        return {"interests": interests, "registers": registers,
                "donations": get_donations().for_person(pid), "groups": get_groups().for_person(pid),
                "directorships": _mark_declared(get_directorships().for_person(pid),
                                               json.dumps([interests, registers], default=str))}

    @app.get("/api/postcode/{postcode}")
    def postcode(postcode: str, request: Request, user=Depends(current_user)):
        requester(request, user)
        key = "postcode:" + re.sub(r"\s+", "", postcode).upper()[:10]
        try:  # seats change only at boundary reviews; kept in memory for a day, never written down
            found = remembered(key, lambda: get_people().by_postcode(postcode), ttl=86400)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        except Exception:  # Parliament's service could not be reached
            raise HTTPException(503, "The postcode service could not be reached. Please try again.") from None
        if not found:
            raise HTTPException(404, "No constituency was found for that postcode.")
        return found

    @app.get("/people/{number}", include_in_schema=False)
    @app.get("/people/{number}/{name}", include_in_schema=False)
    def person_page(number: int, request: Request, name: str = ""):
        """The person page, with that member's name and description in the page head."""
        page_file = dist / "person.html"
        html_doc = page_file.read_text() if page_file.is_file() else "<!doctype html><html><head></head><body></body></html>"
        pp = get_people()
        pid = pp.resolve(number)
        info = pp.page(pid) if pid else None
        base = str(request.base_url).rstrip("/")
        if info is None:
            return Response(page_with_meta(html_doc, title="Member not found — Where They Stand",
                                           description="There is no member at this address.",
                                           url=f"{base}/people/{number}", image=None), status_code=404,
                            media_type="text/html")
        desc = f"{info['status']}. What {info['name']} has said in Parliament, with roles, majorities and votes."
        # contributions only when already at hand, so the page is never held up by them
        text = readable(lambda: person_text(remembered(f"person:{number}:core", lambda: person_core(number)),
                                            remembered_if_ready(f"person:{number}:activity")))
        page_url = f"{base}/people/{info['number']}/{info['slug']}"
        person = {"@context": "https://schema.org", "@type": "Person", "name": info["name"], "url": page_url,
                  "description": info["status"],
                  **({"alternateName": info["other_names"]} if info.get("other_names") else {}),
                  **({"memberOf": {"@type": "Organization", "name": "House of Commons" if info["current"]["house"]
                                   == "commons" else "House of Lords"}} if info.get("current") else {}),
                  **({"affiliation": {"@type": "Organization", "name": info["current"]["party"]}}
                     if (info.get("current") or {}).get("party") else {})}
        return Response(with_text(page_with_meta(html_doc, title=f"{info['name']} — Where They Stand", description=desc,
                                                 url=page_url, image=f"{base}/og-image.png", image_alt="Where They Stand",
                                                 noindex=False, data=person), text), media_type="text/html")

    @app.get("/sitemap-people.xml", include_in_schema=False)
    def sitemap_people(request: Request):
        base = str(request.base_url).rstrip("/")
        urls = "".join(f"<url><loc>{base}/people/{p['number']}/{p['slug']}</loc><changefreq>weekly</changefreq></url>"
                       for p in get_people().search(current_only=True))
        return Response('<?xml version="1.0" encoding="UTF-8"?><urlset '
                        f'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>',
                        media_type="application/xml")

    def sitemap(urls: list[str]) -> Response:
        body = "".join(f"<url><loc>{u}</loc></url>" for u in urls[:50000])
        return Response('<?xml version="1.0" encoding="UTF-8"?><urlset '
                        f'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</urlset>', media_type="application/xml")

    @app.get("/sitemap-committees.xml", include_in_schema=False)
    def sitemap_committees(request: Request):
        base = str(request.base_url).rstrip("/")
        try:
            cs, iq = get_committees().sitemap_ids()
        except HTTPException:
            cs, iq = [], []
        try:  # transcripts collected so far
            ev = sorted(get_evidence().available(), reverse=True)
        except Exception:
            ev = []
        return sitemap([f"{base}/committees/{c}" for c in cs] + [f"{base}/inquiries/{i}" for i in iq]
                       + [f"{base}/evidence/{e}" for e in ev])

    @app.get("/sitemap-groups.xml", include_in_schema=False)
    def sitemap_groups(request: Request):
        base = str(request.base_url).rstrip("/")
        with get_groups().connect() as con:
            rows = con.execute("SELECT parliament, slug FROM groups").fetchall() if con is not None else []
        return sitemap([f"{base}/groups/{p}/{s}" for p, s in rows])

    @app.get("/sitemap-edms.xml", include_in_schema=False)
    def sitemap_edms(request: Request):
        base = str(request.base_url).rstrip("/")
        with get_edms().connect() as con:
            ids = [r[0] for r in con.execute("SELECT id FROM edms ORDER BY date_tabled DESC LIMIT 50000")]
        return sitemap([f"{base}/edms/{i}" for i in ids])

    @app.get("/sitemap-devolved.xml", include_in_schema=False)
    def sitemap_devolved(request: Request):
        from hansard.devolved import LEGISLATURES

        base = str(request.base_url).rstrip("/")
        store, _ = get_devolved()
        with store.connect() as con:
            keys = con.execute("SELECT legislature, key FROM files ORDER BY date DESC").fetchall()
        return sitemap([f"{base}/devolved/{leg}/{key}" for leg, key in keys if leg in LEGISLATURES])

    # ---- RSS feeds -------------------------------------------------------------------------
    def feed(request: Request, path: str, title: str, link: str, description: str, items: list) -> Response:
        base = str(request.base_url).rstrip("/")
        return Response(rss(title=f"{title} — Where They Stand", link=base + link, description=description,
                            self_url=base + path, items=items), media_type=FEED_TYPE)

    @app.get("/feeds/people/{number}.xml", include_in_schema=False)
    def feed_person(number: int, request: Request):
        pp = get_people()
        pid = pp.resolve(number)
        page = pp.page(pid) if pid else None
        if page is None:
            raise HTTPException(404, "There is no member with that number.")
        base = str(request.base_url).rstrip("/")
        items = [{"title": c["heading"] or c["chamber"], "date": c["date"], "guid": c["record_id"],
                  "link": base + c["debate_path"] if c.get("debate_path") else c["twfy_url"],
                  "description": f"{c['chamber']}, {fmt_date(c['date'])}. {c['excerpt'].strip()}…"}
                 for c in pp.contributions(pid)]
        return feed(request, f"/feeds/people/{number}.xml", page["name"], f"/people/{number}/{page['slug']}",
                    f"What {page['name']} has said in Parliament, newest first, from the official record.", items)

    @app.get("/feeds/topics.xml", include_in_schema=False)
    def feed_topics(request: Request):
        from hansard.topics import topic_listing

        base = str(request.base_url).rstrip("/")
        items = [{"title": f"{t['title']}: updated briefing", "link": f"{base}/topics/{t['slug']}",
                  "guid": f"{base}/b/{t['current']['briefing_id']}", "date": t["current"]["prepared_at"],
                  "description": t["current"].get("summary") or t["question"]}
                 for t in topic_listing(settings, state) if t["current"]]
        items.sort(key=lambda i: i["date"], reverse=True)
        return feed(request, "/feeds/topics.xml", "Topic briefings", "/topics",
                    "Each topic briefing when it is updated with new contributions.", items)

    @app.get("/feeds/topics/{slug}.xml", include_in_schema=False)
    def feed_topic(slug: str, request: Request):
        from hansard.topics import versions

        t = _topic(slug)
        base = str(request.base_url).rstrip("/")
        items = []
        for v in versions(state, slug)[:20]:
            b = get_briefing(state, v["briefing_id"])
            summary = summary_text(public_view(b)["answer"]) if b else ""
            items.append({"title": f"{t.title}: briefing of {fmt_date(v['prepared_at'][:10])}",
                          "link": f"{base}/b/{v['briefing_id']}", "guid": f"{base}/b/{v['briefing_id']}",
                          "date": v["prepared_at"], "description": summary or t.question})
        return feed(request, f"/feeds/topics/{slug}.xml", t.title, f"/topics/{slug}",
                    f"Each new version of the topic briefing on {t.title.lower()}, as Parliament says more.", items)

    @app.get("/feeds/bills.xml", include_in_schema=False)
    def feed_bills(request: Request):
        base = str(request.base_url).rstrip("/")
        items = []
        for b in get_bills().all(state.latest_record_date())[:40]:
            last = b["sittings"][-1]
            house = "Lords" if last["house"] == "lords" else "Commons"
            items.append({"title": f"{b['name']}: {', '.join(last['stages'])} ({house})",
                          "link": f"{base}/bills/{b['slug']}", "guid": f"{base}/bills/{b['slug']}#{last['date']}",
                          "date": last["date"],
                          "description": f"Debated in the {house} on {fmt_date(last['date'])}."})
        return feed(request, "/feeds/bills.xml", "Bills", "/bills",
                    "Bills as they are debated in Parliament, latest sitting first.", items)

    @app.get("/feeds/corrections.xml", include_in_schema=False)
    def feed_corrections(request: Request):
        base = str(request.base_url).rstrip("/")
        items = [{"title": "Briefing withdrawn" if c["kind"] == "withdrawn" else "Correction",
                  "link": f"{base}/corrections", "guid": f"{base}/corrections#{c['date']}-{k}", "date": c["date"],
                  "description": " ".join(x for x in (c.get("question"), c.get("note")) if x)}
                 for k, c in enumerate(corrections(state))]
        return feed(request, "/feeds/corrections.xml", "Corrections", "/corrections",
                    "Withdrawn briefings and published corrections.", items)

    @app.get("/feeds/committee-reports.xml", include_in_schema=False)
    def feed_committee_reports(request: Request):
        base = str(request.base_url).rstrip("/")
        try:
            pubs = get_committees().recent_publications()
        except HTTPException:
            pubs = []
        items = [{"title": f"{p['type']}: {p['title']}", "date": p["date"], "guid": f"committee-publication-{p['id']}",
                  "link": f"{base}/inquiries/{p['inquiry_id']}" if p["inquiry_id"] else p["url"],
                  "description": f"{p['type']} from the {p['committee'] or 'committee'}, published "
                                 f"{fmt_date(p['date'])}. The document: {p['url']}"} for p in pubs]
        return feed(request, "/feeds/committee-reports.xml", "Committee reports and government responses",
                    "/committees", "New select committee reports, special reports and government responses.", items)

    @app.get("/feeds/edms.xml", include_in_schema=False)
    def feed_edms(request: Request):
        base = str(request.base_url).rstrip("/")
        _, rows = get_edms().search(limit=40)
        items = [{"title": r["title"], "date": r["date_tabled"] or "2000-01-01", "guid": f"edm-{r['id']}",
                  "link": f"{base}/edms/{r['id']}",
                  "description": f"Early day motion {r['uin'] or ''} tabled by {r['primary_name'] or 'an MP'}"
                                 f"{' (' + r['primary_party'] + ')' if r['primary_party'] else ''}."}
                 for r in rows if r["date_tabled"]]
        return feed(request, "/feeds/edms.xml", "Early day motions", "/edms",
                    "Early day motions as they are tabled.", items)

    @app.get("/changelog", include_in_schema=False)
    def changelog_gone():  # the changelog page was retired: old links go to the About page
        return RedirectResponse("/about", status_code=301)

    # ---- feedback, visit counts and public status ---------------------------------------
    @app.post("/api/feedback")
    def feedback(body: FeedbackBody, request: Request, user=Depends(current_user)):
        visitor, _ = requester(request, user)
        record_feedback(state, body.briefing_key, body.question, body.useful, visitor)
        return {"ok": True}

    @app.post("/api/report")
    def report(body: ReportBody, request: Request, user=Depends(current_user)):
        visitor, _ = requester(request, user)
        if body.website:  # a bot: say thanks, keep nothing
            return {"ok": True}
        if recent_count(state, visitor) >= DAILY_LIMIT:
            raise HTTPException(429, "Thank you, we have your reports. Please email us if there is more to add.")
        bid = body.briefing_id if body.briefing_id and ID_PATTERN.match(body.briefing_id) else None
        add_report(state, body.kind, body.message.strip(), bid, (body.contact or "").strip() or None, visitor)
        return {"ok": True}

    @app.get("/api/corrections")
    def corrections_log():
        return {"corrections": corrections(state)}

    @app.get("/api/admin/reports")
    def reports_list(status: str | None = None, user=Depends(admin_user)):
        return {"reports": list_reports(state, status if status in ("open", *CLOSED) else None)}

    @app.post("/api/admin/reports/{report_id}/close")
    def report_close(report_id: int, body: CloseReportBody, user=Depends(admin_user)):
        if not close_report(state, report_id, body.status, body.resolution, body.public_note):
            raise HTTPException(404, "No such report.")
        return {"ok": True}

    @app.post("/api/hit")
    def hit(body: HitBody, request: Request, user=Depends(current_user)):
        """Cookie-free page counting: no identifiers are sent or stored beyond today's salted hash. Over the allowance
        the page is simply not counted (no error, so nothing shows in the reader's browser)."""
        visitor = None if user else visitor_key(state, request.client.host if request.client else None)
        if not hit_limiter.allow(f"user:{user['user_id']}" if user else f"visitor:{visitor}"):
            return Response(status_code=204)
        if body.event:
            record_event(state, body.event, visitor)
            return {"ok": True}
        host = urlparse(body.referrer).hostname if body.referrer else None
        own = request.url.hostname
        record_visit(state, body.path.split("?")[0], host if host and host != own else None, visitor)
        return {"ok": True}

    @app.get("/api/status")
    def status():
        p = get_pipeline()
        runs = state.runs(1)
        idx = p.engine.index.stats()
        model_on = p.has_model()
        paused = check_caps(state, None, settings.usage, None) if model_on else None

        def latest_vote() -> str | None:
            if not p.votes.available:
                return None
            row = p.votes.con.execute("SELECT max(date) FROM divisions").fetchone()
            return row[0].isoformat() if row and row[0] else None

        return {
            "archive_start": settings.archive_start.isoformat(),
            "latest_record": state.latest_record_date(),
            "latest_vote": remembered("status:latest_vote", latest_vote, ttl=600),
            "last_update": _public_run(runs[0]) if runs else None,
            "briefings": "unavailable" if not model_on else ("paused" if paused else "available"),
            "briefings_note": paused,
            "passages": idx.get("passages", 0),
            "meaning_search_coverage": round(idx["vectors"] / idx["passages"], 3) if idx.get("passages") else 0,
        }

    @app.get("/api/admin/usage")
    def usage(user=Depends(admin_user)):
        from hansard.offsite import backup_alerts

        backups = Path(os.environ.get("HANSARD_BACKUP_DIR", "/backups"))
        return {**summary(state), "visits": visit_summary(state), "tasks": task_summary(state),
                "alerts": alerts(state, settings.usage) + backup_alerts(backups),
                "failures": recent_failures(state), "popular_questions": popular_questions(state)}

    @app.get("/api/admin/sync")
    def sync_log(user=Depends(admin_user)):
        return {"runs": state.runs(30)}

    # ---- web app -------------------------------------------------------------------
    @app.middleware("http")
    async def head_requests(request: Request, call_next):
        """HEAD (used by link checkers and search engines) answers like GET, without the body."""
        if request.method != "HEAD":
            return await call_next(request)
        request.scope["method"] = "GET"
        resp = await call_next(request)
        return Response(status_code=resp.status_code, headers=dict(resp.headers))

    if dist.exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path.startswith("api/"):
                return JSONResponse({"detail": "Not found"}, status_code=404)
            f = (dist / path).resolve()
            if path and f.is_file() and f.is_relative_to(dist.resolve()):  # never serve outside the build
                return FileResponse(f)
            page = dist / f"{path.strip('/')}.html"  # static pages: /methodology -> methodology.html
            if path and "/" not in path.strip("/") and page.is_file() and page.name != "404.html":
                return FileResponse(page)
            if path.strip("/") in ("", "index.html"):
                return FileResponse(dist / "index.html")
            # anything else is not a page: say so, so search engines do not index it as one
            missing = dist / "404.html"
            return FileResponse(missing if missing.is_file() else dist / "index.html", status_code=404)

    if os.environ.get("HANSARD_WARM_UP") == "1":
        # Load the search models and the commonly used tables now, in the background, so the first reader after a
        # restart does not wait for them. Anything that fails here simply loads on first use as before.
        def warm_up() -> None:
            t = time.time()
            try:
                from hansard.engine import Filters

                engine = get_pipeline().engine
                engine.search("cost of living", Filters(), Scope(written=True), [], 0.0, 1.0, 5)
                get_people()
                get_weeks().latest()
                get_bills()
                # what an MP's page draws on, so the first page after a restart is not the slow one
                from hansard.vote_types import vote_types as _vote_types

                _vote_types(settings).all()
                for load in (get_register, get_registers, get_devolved, get_donations, get_groups, get_committees,
                             get_edms, get_directorships):
                    try:
                        load()
                    except Exception as exc:  # a source not set up on this server: load it on first use as before
                        log.info("warm-up skipped %s: %s", load.__name__, exc)
                log.info("warmed up in %.1fs", time.time() - t)
            except Exception as exc:  # never stop the site starting
                log.warning("warm-up incomplete: %s", exc)

        threading.Thread(target=warm_up, name="warm-up", daemon=True).start()
    return app
