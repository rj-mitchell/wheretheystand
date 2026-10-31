"""Web API: public access with caps and cache, optional sign-in, streaming answers, search."""

from __future__ import annotations

import dataclasses
import json

import pytest
from fastapi.testclient import TestClient

from hansard.api.app import create_app
from hansard.api.auth import create_user
from hansard.api.usage import cost_usd, record_usage
from hansard.embed.base import HashingEmbedder
from hansard.engine import Engine
from hansard.rag.pipeline import Pipeline
from hansard.store.state import StateDB

from .test_rag import TODAY, FakeClient


def make_app(indexed_settings, tmp_path, *, auth: bool, **usage):
    # each test gets its own state DB (users, usage, cache) on top of the shared index
    s = dataclasses.replace(indexed_settings, raw={
        **indexed_settings.raw,
        "auth": {"required": auth},
        "usage": {**indexed_settings.raw["usage"], "per_user_daily_questions": 2, "per_visitor_daily_questions": 2,
                  **usage}})
    client = FakeClient()
    pipeline = Pipeline(s, Engine(s, embedder=HashingEmbedder(64), today=TODAY), client=client, today=TODAY)
    state = StateDB(tmp_path / "state.sqlite")
    return create_app(s, pipeline, state), state, client


@pytest.fixture()
def api(indexed_settings, tmp_path):
    """Invite-only mode."""
    app, state, _ = make_app(indexed_settings, tmp_path, auth=True, answer_cache_hours=0)
    create_user(state, "researcher@example.org", "correct horse battery")
    create_user(state, "admin@example.org", "admin password 123", admin=True)
    return TestClient(app), state


@pytest.fixture()
def public(indexed_settings, tmp_path):
    """The default: open to everyone."""
    app, state, client = make_app(indexed_settings, tmp_path, auth=False)
    return TestClient(app), state, client


def login(client, email="researcher@example.org", password="correct horse battery"):
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r


def events(resp):
    out = []
    for chunk in resp.text.split("\n\n"):
        for line in chunk.splitlines():
            if line.startswith("data: "):
                out.append(json.loads(line[6:]))
    return out


def test_requires_sign_in(api):
    client, _ = api
    assert client.get("/api/meta").status_code == 401
    assert client.post("/api/ask", json={"question": "trial by jury"}).status_code == 401
    assert client.get("/api/health").status_code == 200


def test_login_logout_and_bad_password_throttle(api):
    client, _ = api
    for _ in range(5):
        assert client.post("/api/auth/login", json={"email": "researcher@example.org", "password": "nope"}).status_code == 401
    assert client.post("/api/auth/login", json={"email": "researcher@example.org", "password": "nope"}).status_code == 429
    client2 = TestClient(client.app)
    client2.headers["X-Forwarded-For"] = "x"
    # the throttle is keyed on client + email; a different email still works
    login(client2, "admin@example.org", "admin password 123")
    assert client2.get("/api/me").json()["email"] == "admin@example.org"
    client2.post("/api/auth/logout")
    assert client2.get("/api/me").status_code == 401


def test_meta_lists_filters_and_attribution(api):
    client, _ = api
    login(client)
    m = client.get("/api/meta").json()
    assert "North East" in m["regions"] and "transport" in m["portfolios"]
    assert any("Open Parliament Licence" in a["text"] for a in m["attribution"])
    assert any("Open Government Licence" in a["text"] for a in m["attribution"])
    assert any("mySociety" in a["text"] for a in m["attribution"])


def test_ask_streams_plan_sources_text_and_verified_answer(api):
    client, state = api
    login(client)
    r = client.post("/api/ask", json={"question": "What has been said about trial by jury?"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    evs = events(r)
    types = [e["type"] for e in evs]
    assert evs[0]["type"] == "progress" and evs[0]["step"] == "reading"  # sent before the question is read
    assert types[1] == "plan" and types[2] == "sources" and "text" in types and types[-1] == "final"
    assert types[-2] == "progress"  # checking the citations, after the writing
    final = evs[-1]["answer"]
    assert "[Source 1]" in final["markdown"] and final["citations_invalid"] == 0
    with state.connect() as con:
        assert con.execute("SELECT count(*) FROM usage WHERE kind = 'answer'").fetchone()[0] == 1


def sources_but_no_briefing(evs, word):
    """Over a limit, readers still get the (free) sources, with a note saying why there is no briefing."""
    final = evs[-1]
    return (final["type"] == "final" and final["answer"] is None and word in final["note"]
            and any(e["type"] == "sources" and e["sources"] for e in evs))


def test_sources_only_needs_no_model(public):
    client, state, fake = public
    before = len(fake.calls)
    evs = events(client.post("/api/ask", json={"question": "trial by jury", "sources_only": True}))
    assert sources_but_no_briefing(evs, "Sources only") and len(fake.calls) == before


def test_daily_question_cap(api):
    client, _ = api
    login(client)
    for _ in range(2):
        assert events(client.post("/api/ask", json={"question": "trial by jury"}))[-1]["type"] == "final"
    last = events(client.post("/api/ask", json={"question": "trial by jury"}))
    assert sources_but_no_briefing(last, "limit")


def test_search_endpoint_needs_no_model(api):
    client, _ = api
    login(client)
    r = client.post("/api/search", json={"question": "Northern Ireland security", "scope": {"written": False}})
    body = r.json()
    assert body["sources"] and all(s["kind"] == "record" for s in body["sources"])
    assert all("written" not in s["chamber"].lower() for s in body["sources"])
    assert "keyword;dur=" in r.headers["server-timing"] and "timings" not in body


def test_admin_usage_is_admin_only(api):
    client, state = api
    login(client)
    assert client.get("/api/admin/usage").status_code == 403
    admin = TestClient(client.app)
    login(admin, "admin@example.org", "admin password 123")
    record_usage(state, None, "answer", "claude-opus-5-5", {"input_tokens": 10_000, "output_tokens": 1_000}, {})
    assert admin.get("/api/admin/usage").json()["total_usd"] > 0


def test_cost_calculation():
    assert cost_usd({"input_tokens": 1_000_000, "output_tokens": 100_000, "cache_read_tokens": 0},
                    {"price_input_per_mtok": 4.0, "price_output_per_mtok": 20.0}) == pytest.approx(6.0)


# ---- public mode (the default) -------------------------------------------------------------------


def test_public_site_needs_no_sign_in(public):
    client, state, _ = public
    assert client.get("/api/me").json() == {"email": None, "is_admin": False, "auth_required": False}
    assert client.get("/api/meta").status_code == 200
    evs = events(client.post("/api/ask", json={"question": "What has been said about trial by jury?"}))
    assert evs[-1]["type"] == "final" and evs[-1]["answer"]["citations_total"] == 1
    with state.connect() as con:
        visitor = con.execute("SELECT visitor FROM usage WHERE kind = 'answer'").fetchone()[0]
    assert visitor and "testclient" not in visitor  # a salted hash, never the address


def test_repeat_questions_come_from_the_cache_and_cost_nothing(public):
    client, state, model = public
    first = events(client.post("/api/ask", json={"question": "What has been said about trial by jury?"}))
    again = events(client.post("/api/ask", json={"question": "what has been said about  trial by jury?"}))
    assert len(model.calls) == 1
    assert again[-1]["cached"] is True and again[-1]["answer"] == first[-1]["answer"]
    assert [e["type"] for e in again] == ["plan", "sources", "text", "final"]
    other = events(client.post("/api/ask", json={"question": "What has been said about trial by jury?",
                                                 "mode": "comprehensive"}))
    assert len(model.calls) == 2 and "cached" not in other[-1]
    with state.connect() as con:
        assert con.execute("SELECT count(*) FROM usage WHERE kind = 'answer'").fetchone()[0] == 2


def test_each_visitor_has_a_daily_limit_but_cached_answers_still_load(public):
    client, _, _ = public
    for q in ("trial by jury", "jury trials and courts"):
        assert events(client.post("/api/ask", json={"question": q}))[-1]["type"] == "final"
    blocked = events(client.post("/api/ask", json={"question": "security in Northern Ireland"}))
    assert sources_but_no_briefing(blocked, "limit")
    assert events(client.post("/api/ask", json={"question": "trial by jury"}))[-1]["type"] == "final"
    other = TestClient(client.app, client=("203.0.113.9", 50000))
    assert events(other.post("/api/ask", json={"question": "security in Northern Ireland"}))[-1]["type"] == "final"


def test_site_wide_daily_budget(public):
    client, state, _ = public
    record_usage(state, None, "answer", "claude-opus-5-5", {"input_tokens": 3_000_000}, {})
    evs = events(client.post("/api/ask", json={"question": "trial by jury"}))
    assert sources_but_no_briefing(evs, "budget")


def test_requests_per_minute_limit(indexed_settings, tmp_path):
    app, _, _ = make_app(indexed_settings, tmp_path, auth=False, requests_per_minute=3)
    client = TestClient(app)
    codes = [client.post("/api/search", json={"question": "trial by jury"}).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]


def test_admin_endpoints_need_the_admin_token(indexed_settings, tmp_path, monkeypatch):
    monkeypatch.setenv("HANSARD_ADMIN_TOKEN", "s3cret-token")
    app, _, _ = make_app(indexed_settings, tmp_path, auth=False)
    client = TestClient(app)
    assert client.get("/api/admin/usage").status_code == 403
    assert client.get("/api/admin/usage", headers={"Authorization": "Bearer wrong"}).status_code == 403
    assert client.get("/api/admin/sync", headers={"Authorization": "Bearer s3cret-token"}).status_code == 200


def test_feedback_refers_to_the_briefing_and_shows_in_admin_usage(indexed_settings, tmp_path, monkeypatch):
    monkeypatch.setenv("HANSARD_ADMIN_TOKEN", "s3cret-token")
    app, state, _ = make_app(indexed_settings, tmp_path, auth=False)
    client = TestClient(app)
    final = events(client.post("/api/ask", json={"question": "trial by jury"}))[-1]
    key = final["briefing_key"]
    assert key and client.post("/api/ask", json={"question": "trial by jury"}).text.count(key) == 1  # replay too
    assert client.post("/api/feedback", json={"question": "trial by jury", "useful": False,
                                              "briefing_key": key}).json() == {"ok": True}
    usage = client.get("/api/admin/usage", headers={"Authorization": "Bearer s3cret-token"}).json()
    assert usage["visits"]["feedback"]["total"] == 1
    assert usage["visits"]["feedback"]["recent_not_useful"][0]["question"] == "trial by jury"


def test_visit_counts_keep_daily_totals_only(public):
    client, state, _ = public
    for path in ("/", "/", "/methodology"):
        assert client.post("/api/hit", json={"path": path, "referrer": "https://news.example.org/story"}).status_code == 200
    with state.connect() as con:
        assert con.execute("SELECT sum(views) FROM page_views").fetchone()[0] == 3
        assert tuple(con.execute("SELECT host, views FROM referrers").fetchone()) == ("news.example.org", 3)
        assert con.execute("SELECT count(*) FROM day_visitors").fetchone()[0] == 1  # one visitor, hashed
        con.execute("UPDATE day_visitors SET day = '2026-01-01'")  # yesterday's hashes...
    client.post("/api/hit", json={"path": "/"})
    with state.connect() as con:  # ...are rolled up into a count and deleted
        assert con.execute("SELECT visitors FROM day_totals WHERE day = '2026-01-01'").fetchone()[0] == 1
        assert con.execute("SELECT count(*) FROM day_visitors WHERE day = '2026-01-01'").fetchone()[0] == 0


def test_what_visitors_did_is_counted_once_a_day_each(public):
    from hansard.api.usage import task_summary

    client, state, _ = public
    client.post("/api/hit", json={"path": "/"})
    for _ in range(2):  # opening two sources counts the visitor once
        assert client.post("/api/hit", json={"path": "/b/x", "event": "source"}).status_code == 200
    assert client.post("/api/hit", json={"path": "/", "event": "something-else"}).status_code == 422
    with state.connect() as con:
        assert con.execute("SELECT count(*) FROM day_events WHERE event = 'source'").fetchone()[0] == 1
        assert con.execute("SELECT sum(views) FROM page_views").fetchone()[0] == 1  # an event is not a page view
        con.execute("UPDATE day_events SET day = '2026-01-01'")
        con.execute("UPDATE day_visitors SET day = '2026-01-01'")
    client.post("/api/hit", json={"path": "/b/x", "event": "source"})  # rolls up yesterday's
    with state.connect() as con:
        assert con.execute("SELECT visitors FROM event_totals WHERE day = '2026-01-01'").fetchone()[0] == 1
        assert con.execute("SELECT count(*) FROM day_events WHERE day = '2026-01-01'").fetchone()[0] == 0
    t = task_summary(state)
    assert t["source"]["visitors"] == 1 and t["ask"]["visitors"] == 0 and t["useful"]["rated"] == 0


def test_public_status(public):
    client, state, _ = public
    st = client.get("/api/status").json()
    assert st["briefings"] == "available" and st["archive_start"]
    record_usage(state, None, "answer", "claude-opus-5-5", {"input_tokens": 3_000_000}, {})
    st = client.get("/api/status").json()
    assert st["briefings"] == "paused" and "budget" in st["briefings_note"]


def test_static_pages_are_served_at_clean_addresses(indexed_settings, tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("app")
    (dist / "methodology.html").write_text("method")
    (dist / "robots.txt").write_text("robots")
    s = dataclasses.replace(indexed_settings, raw={**indexed_settings.raw,
                                                   "paths": {**indexed_settings.raw.get("paths", {}), "web_dist": str(dist)}})
    app, _, _ = make_app(s, tmp_path, auth=False)
    client = TestClient(app)
    assert client.get("/methodology").text == "method"
    assert client.get("/robots.txt").text == "robots"
    assert client.get("/").text == "app"
    missing = client.get("/no/such/page")  # not a page: a real 404, so search engines do not index it
    assert missing.status_code == 404 and missing.text == "app"  # (no 404.html in this build: the app)
    (dist / "404.html").write_text("not found")
    assert client.get("/no-such-page").text == "not found" and client.get("/404").status_code == 404
    assert client.get("/api/nope").status_code == 404
    head = client.head("/methodology")  # link checkers and search engines ask with HEAD
    assert head.status_code == 200 and head.text == ""


def test_public_endpoints_show_only_when_and_whether_updates_ran(public):
    client, state, _ = public
    run = state.start_run("sync")
    state.finish_run(run, {"reference": {"parlparse_sha": "abc123"}})
    for body in (client.get("/api/status").json()["last_update"], client.get("/api/meta").json()["last_sync"]):
        assert set(body) == {"finished_at", "status"} and body["status"] == "ok"


def _app_with_dist(indexed_settings, tmp_path, monkeypatch=None):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(
        '<!doctype html><html><head><title>Where MPs Stand</title>'
        '<meta property="og:title" content="Where MPs Stand" />'
        '<meta property="og:image" content="https://wheretheystand.uk/og-image.png" />'
        '<link rel="canonical" href="https://wheretheystand.uk/" /></head><body>app</body></html>')
    s = dataclasses.replace(indexed_settings, raw={
        **indexed_settings.raw, "paths": {**indexed_settings.raw.get("paths", {}), "web_dist": str(dist)}})
    app, state, _ = make_app(s, tmp_path, auth=False)
    return TestClient(app), state


def test_every_briefing_gets_a_permanent_shareable_address(indexed_settings, tmp_path, monkeypatch):
    monkeypatch.setenv("HANSARD_ADMIN_TOKEN", "s3cret-token")
    client, _ = _app_with_dist(indexed_settings, tmp_path)
    final = events(client.post("/api/ask", json={"question": "What has been said about trial by jury?"}))[-1]
    bid = final["briefing_id"]
    assert len(bid) == 10 and final["prepared_at"]
    # a repeat question replays the same briefing, at the same address
    again = events(client.post("/api/ask", json={"question": "What has been said about trial by jury?"}))[-1]
    assert again["cached"] and again["briefing_id"] == bid

    view = client.get(f"/api/briefings/{bid}").json()
    assert view["question"] == "What has been said about trial by jury?"
    assert view["answer"]["citations_total"] == 1 and view["sources"] and view["plan"]
    assert "usage" not in json.dumps(view) and "briefing_key" not in view

    page = client.get(f"/b/{bid}")
    assert page.status_code == 200 and "<body>app</body>" in page.text
    assert '<meta property="og:title" content="What has been said about trial by jury? — Where MPs Stand" />' in page.text
    # shared links show the site's own image; the briefing's card is still there for embedding
    assert 'og-image.png" />' in page.text and 'content="noindex"' in page.text
    assert page.text.count("<title>") == 1 and page.text.count('property="og:image"') == 1  # site-wide tags replaced

    card = client.get(f"/b/{bid}/card.png")
    assert card.status_code == 200 and card.headers["content-type"] == "image/png"
    from io import BytesIO

    from PIL import Image
    assert Image.open(BytesIO(card.content)).size == (1200, 630)

    # withdrawing a briefing takes down the page, the data and the card
    admin = {"Authorization": "Bearer s3cret-token"}
    assert client.post(f"/api/admin/briefings/{bid}/withdraw", json={"reason": "complaint"}).status_code == 403
    assert client.post(f"/api/admin/briefings/{bid}/withdraw", json={"reason": "complaint"}, headers=admin).json()
    assert client.get(f"/api/briefings/{bid}").status_code == 410
    assert client.get(f"/b/{bid}").status_code == 410
    assert client.get(f"/b/{bid}/card.png").status_code == 410
    listed = client.get("/api/admin/briefings", headers=admin).json()["briefings"]
    assert listed[0]["id"] == bid and listed[0]["withdrawn_reason"] == "complaint"


def test_unknown_or_malformed_briefing_addresses(indexed_settings, tmp_path):
    client, _ = _app_with_dist(indexed_settings, tmp_path)
    assert client.get("/api/briefings/abcdefghij").status_code == 404
    assert client.get("/b/abcdefghij").status_code == 404
    assert client.get("/b/ZZZ'%3Cscript%3E").status_code == 404


def test_preview_text_starts_with_the_summary():
    from hansard.api.briefings import summary_text

    md = "# Onshore wind: city and rural MPs\n\n**Headline:** In the records retrieved, rural MPs objected [Source 3].\n\n**Ministers - supportive**"
    assert summary_text({"markdown": md}).startswith("Rural MPs objected.")  # no title, no remark about the records
    titled = "**Cost of living: positions in Parliament, 2025 to 2026**\n\n**In brief**\n- In the records retrieved, ministers spoke."
    assert summary_text({"markdown": titled}) == "Ministers spoke."
    assert summary_text({"markdown": "Headline\n\n" + "word " * 100}, limit=40).endswith("…")


def test_reports_are_private_until_closed_with_a_public_note(indexed_settings, tmp_path, monkeypatch):
    monkeypatch.setenv("HANSARD_ADMIN_TOKEN", "s3cret-token")
    app, state, _ = make_app(indexed_settings, tmp_path, auth=False)
    client = TestClient(app)
    admin = {"Authorization": "Bearer s3cret-token"}
    final = events(client.post("/api/ask", json={"question": "What has been said about trial by jury?"}))[-1]
    bid = final["briefing_id"]

    ok = client.post("/api/report", json={"kind": "correction", "message": "The second point misquotes the minister.",
                                           "briefing_id": bid, "contact": "reader@example.org"})
    assert ok.status_code == 200
    assert client.post("/api/report", json={"kind": "general", "message": "too short"}).status_code == 422
    # a spam bot fills in the hidden field: thanked, but nothing is kept
    assert client.post("/api/report", json={"kind": "general", "message": "Buy cheap watches now!!",
                                            "website": "http://spam.example"}).status_code == 200

    assert client.get("/api/admin/reports").status_code == 403
    reports = client.get("/api/admin/reports?status=open", headers=admin).json()["reports"]
    assert len(reports) == 1 and reports[0]["briefing_id"] == bid and reports[0]["contact"] == "reader@example.org"
    assert client.get("/api/corrections").json()["corrections"] == []

    rid = reports[0]["id"]
    closed = client.post(f"/api/admin/reports/{rid}/close", headers=admin,
                         json={"status": "resolved", "resolution": "Checked; quote was wrong.",
                               "public_note": "A quotation in a briefing on jury trials was corrected."})
    assert closed.status_code == 200
    after = client.get("/api/admin/reports", headers=admin).json()["reports"][0]
    assert after["status"] == "resolved" and after["contact"] is None  # contact details deleted on closing

    client.post(f"/api/admin/briefings/{bid}/withdraw", headers=admin, json={"reason": "Misquoted a minister."})
    log = client.get("/api/corrections").json()["corrections"]
    assert {c["kind"] for c in log} == {"withdrawn", "correction"}
    assert "Checked" not in json.dumps(log)  # the private resolution never appears publicly
    withdrawn = next(c for c in log if c["kind"] == "withdrawn")
    assert withdrawn["note"] == "Misquoted a minister." and withdrawn["briefing_id"] == bid


def test_reports_are_limited_per_visitor(indexed_settings, tmp_path):
    app, _, _ = make_app(indexed_settings, tmp_path, auth=False, requests_per_minute=100)
    client = TestClient(app)
    body = {"kind": "general", "message": "A perfectly reasonable message."}
    codes = [client.post("/api/report", json=body).status_code for _ in range(11)]
    assert codes[:10] == [200] * 10 and codes[10] == 429


def test_donate_link_is_hidden_until_set(indexed_settings, tmp_path, monkeypatch):
    monkeypatch.delenv("HANSARD_DONATE_URL", raising=False)
    app, _, _ = make_app(indexed_settings, tmp_path, auth=False)
    assert TestClient(app).get("/api/site").json() == {"donate_url": "https://ko-fi.com/wheretheystand"}
    unset = dataclasses.replace(indexed_settings, raw={**indexed_settings.raw, "site": {"donate_url": ""}})
    app, _, _ = make_app(unset, tmp_path, auth=False)
    client = TestClient(app)
    assert client.get("/api/site").json() == {"donate_url": None}
    monkeypatch.setenv("HANSARD_DONATE_URL", "https://ko-fi.com/example")
    assert client.get("/api/site").json() == {"donate_url": "https://ko-fi.com/example"}
    assert client.get("/api/meta").json()["donate_url"] == "https://ko-fi.com/example"
    for bad in ("http://ko-fi.com/x", "javascript:alert(1)", 'https://x.example/"><script>'):
        monkeypatch.setenv("HANSARD_DONATE_URL", bad)
        assert client.get("/api/site").json() == {"donate_url": None}


def test_visitor_codes_change_daily_and_old_secrets_are_deleted(tmp_path):
    from hansard.api.usage import visitor_key

    state = StateDB(tmp_path / "state.sqlite")
    state.set("visitor_salt", "an-old-permanent-salt")
    monday = visitor_key(state, "203.0.113.5", day="2026-10-12")
    assert visitor_key(state, "203.0.113.5", day="2026-10-12") == monday
    assert visitor_key(state, "198.51.100.7", day="2026-10-12") != monday
    tuesday = visitor_key(state, "203.0.113.5", day="2026-10-13")
    assert tuesday != monday
    with state.connect() as con:
        keys = [r[0] for r in con.execute("SELECT key FROM kv WHERE key LIKE 'visitor_salt%'")]
    assert keys == ["visitor_salt:2026-10-13"]


def test_failures_and_spending_show_as_alerts(tmp_path):
    from hansard.api.usage import alerts, recent_failures, record_failure

    state = StateDB(tmp_path / "state.sqlite")
    caps = {"daily_spend_cap_usd": 10, "monthly_spend_cap_usd": 150, "price_input_per_mtok": 4.0}
    assert alerts(state, caps) == []
    record_failure(state, "question reading", "Q?", "AuthenticationError: invalid x-api-key")
    a = alerts(state, caps)
    assert len(a) == 1 and "1 briefing failed" in a[0]["text"] and "invalid x-api-key" in a[0]["text"]
    assert recent_failures(state)[0]["kind"] == "question reading"
    record_usage(state, None, "answer", "m", {"input_tokens": 2_000_000}, caps, "v")  # $8 of a $10 day
    texts = " ".join(x["text"] for x in alerts(state, caps))
    assert "80% of the $10 cap" in texts


def test_failed_briefing_is_logged_and_readers_see_plain_words(indexed_settings, tmp_path, monkeypatch):
    monkeypatch.setenv("HANSARD_ADMIN_TOKEN", "s3cret-token")
    app, state, client = make_app(indexed_settings, tmp_path, auth=False)

    def broken(*a, **k):
        raise RuntimeError("upstream is down")

    monkeypatch.setattr(client.messages, "stream", broken)
    monkeypatch.setattr(client.beta.messages, "stream", broken)
    tc = TestClient(app)
    evs = events(tc.post("/api/ask", json={"question": "What has been said about trial by jury?"}))
    err = evs[-1]
    assert err["type"] == "error" and "could not be written" in err["message"] and "RuntimeError" not in err["message"]
    usage = tc.get("/api/admin/usage", headers={"Authorization": "Bearer s3cret-token"}).json()
    assert usage["failures"][0]["error"].startswith("RuntimeError") and usage["alerts"]
    assert usage["popular_questions"][0] == {"question": "what has been said about trial by jury?", "times": 1,
                                              "last": usage["popular_questions"][0]["last"]}


def test_cache_writes_and_reads_are_priced(tmp_path):
    prices = {"price_input_per_mtok": 4.0, "price_output_per_mtok": 20.0, "price_cache_read_per_mtok": 0.2}
    assert cost_usd({"input_tokens": 1_000_000, "cache_write_tokens": 1_000_000}, prices) == 5.0
    assert cost_usd({"input_tokens": 0, "cache_read_tokens": 1_000_000}, prices) == 0.2


def test_support_reminder_every_n_new_briefings(indexed_settings, tmp_path, monkeypatch):
    monkeypatch.setenv("HANSARD_DONATE_URL", "https://ko-fi.com/example")
    app, state, _ = make_app(indexed_settings, tmp_path, auth=False, support_reminder_every=2,
                             per_visitor_daily_questions=10)
    client = TestClient(app)

    def final(q):
        return [e for e in events(client.post("/api/ask", json={"question": q})) if e["type"] == "final"][-1]

    assert "support_reminder" not in final("trial by jury")
    second = final("onshore wind farms")
    assert second["support_reminder"] == 2  # every second new briefing, with the count so far
    assert "support_reminder" not in final("trial by jury")  # an earlier briefing again: no new cost, no reminder
    # the shared copy of a briefing never carries the reminder
    assert "support_reminder" not in client.get(f"/api/briefings/{second['briefing_id']}").text
    assert client.post("/api/support-clicked").status_code == 200
    final("rail fares")
    assert "support_reminder" not in final("prison places")  # went to donate: no more reminders today


def test_pages_carry_readable_text_for_readers_without_scripts():
    from hansard.api.plaintext import briefing_text, markdown_text, with_text

    page = '<body><div id="person-root"><p class="muted">Loading…</p></div></body>'
    assert with_text(page, "<h1>Hilary Benn</h1>") == '<body><div id="person-root"><h1>Hilary Benn</h1></div></body>'
    assert 'class="thread prose"><p>x</p>' in with_text('<div id="root"></div>', "<p>x</p>")
    assert with_text(page, None) == page
    md = markdown_text("**In brief**\n- Ministers said <b>so</b> [Source 1]\n\nAbout the evidence: two records.")
    assert "<strong>In brief</strong>" in md and "<li>Ministers said &lt;b&gt;so&lt;/b&gt;" in md
    text = briefing_text({"question": "Q?", "created_at": "2026-10-01T10:00:00", "records_up_to": "2026-09-30",
                          "answer": {"markdown": "Body."},
                          "sources": [{"n": 1, "title": "A <speech>", "official_url": "https://x.example/1"}]})
    assert "<h1>Q?</h1>" in text and "A &lt;speech&gt;" in text and 'href="https://x.example/1"' in text


def test_oversized_follow_up_history_and_plans_are_bounded(public):
    from hansard.api.app import AskBody

    body = AskBody.model_validate({"question": "trial by jury", "history": [
        {"question": "x" * 600_000, "answer": "y" * 600_000, "topic": "z" * 9_000, "scope": "w" * 9_000}]})
    h = body.history_dicts()[0]
    assert (len(h["question"]), len(h["answer"]), len(h["topic"]), len(h["scope"])) == (2000, 1000, 500, 1000)
    client, _, _ = public
    huge = {"search_text": "jury", "filters": {"person_ids": [str(i) for i in range(100_000)]}}
    assert client.post("/api/search", json={"question": "trial by jury", "plan": huge}).status_code == 422
    from hansard.rag.query_parse import QueryPlan

    plan = QueryPlan.from_dict({"search_text": "a" * 50_000, "filters": {"parties": ["x"] * 500}})
    assert len(plan.search_text) == 1000 and len(plan.filters.parties) == 50


def test_briefings_in_progress_count_against_the_caps():
    from hansard.api.usage import InFlight

    f = InFlight(per_key=2, total=3, estimate_usd=0.5)
    assert f.start("a") is None and f.start("a") is None
    assert "already being written" in f.start("a")
    assert f.start("b") is None and "Many briefings" in f.start("c")
    assert f.pending_usd() == 1.5 and f.pending_usd(besides=1) == 1.0
    f.finish("a")
    assert f.start("c") is None


def test_an_ipv6_connection_counts_as_one_visitor():
    from hansard.api.usage import network_of

    assert network_of("2001:db8:1:2:aaaa::1") == network_of("2001:db8:1:2:bbbb::9") == "2001:db8:1:2::/64"
    assert network_of("2001:db8:1:3::1") != network_of("2001:db8:1:2::1")
    assert network_of("203.0.113.9") == "203.0.113.9" and network_of("::ffff:203.0.113.9") == "203.0.113.9"
    assert network_of(None) == "-"


def test_sign_in_attempts_are_throttled_per_address_whatever_email_is_tried(api):
    client, _ = api
    codes = [client.post("/api/auth/login", json={"email": f"guess{i}@example.org", "password": "wrong"}).status_code
             for i in range(21)]
    assert codes[:20] == [401] * 20 and codes[20] == 429
