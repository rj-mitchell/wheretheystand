from hansard.api.plaintext import (
    committee_text,
    devolved_day_text,
    edm_text,
    group_text,
    inquiry_text,
    with_text,
)


def test_committee_and_inquiry_text():
    c = {"name": "Treasury Committee", "house": "Commons", "category": "Select", "start": "2000-01-01", "end": None,
         "url": "https://x", "purpose": "Scrutinises <the> Treasury.",
         "inquiries": [{"id": 7, "title": "Bank & money", "open": "2025-01-02", "close": None}],
         "top_organisations": [{"organisation": "Bank of England", "n": 4}]}
    html = committee_text(c)
    assert html.startswith("<h1>Treasury Committee</h1>") and "Scrutinises &lt;the&gt; Treasury." in html
    assert '<a href="/inquiries/7">Bank &amp; money</a> (2 January 2025)' in html and "Bank of England (4)" in html
    i = inquiry_text({"title": "Bank & money", "type": "Inquiry", "open": "2025-01-02", "close": "2025-06-01",
                      "url": None, "committees": [{"name": "Treasury Committee"}], "publications": [],
                      "sessions": [{"date": "2025-02-03", "witnesses": [{"name": "A Person", "organisation": "Org"}]}]})
    assert "<h1>Bank &amp; money</h1>" in i and "Inquiry by the Treasury Committee, 2 January 2025 to 1 June 2025" in i
    assert "3 February 2025: A Person, Org" in i


def test_group_devolved_and_motion_text():
    g = group_text({"title": "APPG on Cycling", "purpose": "Promote cycling.", "category": "Subject Group",
                    "categories": None, "secretariat": None, "source_url": "https://y",
                    "members": [{"name": "A MP", "role": "Chair"}],
                    "benefits": [{"funder": "Bikes Ltd", "type": "Benefits In Kind", "value": "1,500-3,000",
                                  "received": "2026-01-01"}]})
    assert "<h1>APPG on Cycling</h1>" in g and "<li>A MP (Chair)</li>" in g and "Bikes Ltd: Benefits In Kind, £1,500-3,000" in g
    d = devolved_day_text({"body": "Senedd", "date": "2026-10-14", "contributions": 1, "speakers": 1,
                           "official_url": None, "items": [{"kind": "heading", "text": "Questions"},
                                                           {"kind": "said", "speaker": {"name": "A MS", "party": "Plaid"},
                                                            "paragraphs": ["Diolch."]}]})
    assert d.startswith("<h1>Senedd, 14 October 2026</h1>") and "<h3>A MS (Plaid)</h3><p>Diolch.</p>" in d
    m = edm_text({"title": "T", "uin": "1", "date_tabled": "2026-01-01", "sponsors_count": 1})
    assert "Signed by 1 member." in m


def test_text_goes_into_the_page_root():
    page = '<body><div id="group-root"><p class="muted">Loading…</p></div></body>'
    assert with_text(page, "<h1>X</h1>") == '<body><div id="group-root"><h1>X</h1></div></body>'
