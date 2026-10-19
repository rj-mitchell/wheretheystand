"""What each division was on: classification from the recorded wording, the cache, and the glossary entries."""

import html
from pathlib import Path

import duckdb

from hansard.store.parquet import attach_store
from hansard.vote_types import KINDS, VoteTypes, classify

GLOSSARY = Path(__file__).resolve().parents[1] / "web" / "glossary.html"


def kind(before="", after="", major="", minor=None, house="commons"):
    return classify(before, after, major, minor, house)["kind"]


def test_second_reading_and_the_amendment_to_decline_it():
    bill = "Safety of Rwanda (Asylum and Immigration) Bill"
    # the reasoned amendment is decided first; the motion it would replace follows the division
    assert kind("Amendment proposed: at the end of the Question to add “this House, … declines to give a Second "
                "Reading to the Safety of Rwanda Bill …”.\nQuestion put, That the amendment be made.",
                "Question accordingly negatived.\nMain Question put, That the Bill be now read a Second time.",
                bill) == "reasoned_amendment"
    out = classify("Main Question put forthwith (Standing Order No. 62(2)).\nQuestion put, That the Bill be now read "
                   "a Second time.", "Question accordingly agreed to.\nBill read a Second time.", bill, None, "commons")
    assert out["kind"] == "second_reading"
    assert out["question"] == "That the Bill be now read a Second time"
    assert "Bill read a Second time" in out["outcome"] and out["note"] == KINDS["second_reading"][1]
    assert kind("", "", "Holocaust Memorial Bill - Second Reading", "Amendment to the Motion",
                "lords") == "reasoned_amendment"


def test_procedural_and_amending_votes():
    assert kind("Question put, That the following provisions shall apply to the Safety of Rwanda Bill:",
                "", "SAFETY OF RWANDA (ASYLUM AND IMMIGRATION) BILL (PROGRAMME)") == "programme"
    assert kind("Question put, That the clause be read a Second time.", "Question accordingly negatived.",
                "Planning Bill", "New Clause 3 - Review") == "new_clause"
    assert kind("Question put, That the amendment be made.", "", "Sentencing Bill", "Clause 4 - Decisions") == \
        "amendment"
    assert kind("", "Amendment 40 agreed.", "Elections Bill - Report (2nd Day)", "Amendment 40",
                "lords") == "amendment", "a Lords amendment heading wins over nearby talk of regret"
    assert kind("Division on whether Clause 12 should stand part of the Bill.", "Clause 12 agreed.",
                "Police Bill - Committee", None, "lords") == "stand_part"
    assert kind("Motion for leave to bring in a Bill (Standing Order No. 23)", "", "Pets Bill") == "ten_minute_rule"
    assert kind("Question put, That this House disagrees with Lords amendment 1.", "", "Rwanda Bill",
                "") == "lords_amendment"
    assert kind("", "", "Benefit Cap Regulations 2016 - Motion to Regret", None, "lords") == "regret_motion"
    assert kind("Question put, That the Question be now put.", "", "Assisted Dying Bill") == "closure"
    assert kind("", "", "Universal Credit", None) == "other"


def test_every_kind_is_explained_in_the_glossary():
    page = GLOSSARY.read_text()
    for k, (_label, note) in KINDS.items():
        if k == "other":
            continue
        assert f'id="vote-{k.replace("_", "-")}"' in page, k
        assert html.escape(note, quote=False) in page, f"the glossary note for {k} differs from vote_types.KINDS"


def test_built_from_the_record_and_cached(indexed_settings):
    con = duckdb.connect()
    attach_store(con, indexed_settings.parquet_dir)
    n = con.execute("SELECT count(*) FROM divisions").fetchone()[0]
    vt = VoteTypes(indexed_settings)
    types = vt.all()
    assert len(types) == n and all(t["kind"] in KINDS for t in types.values())
    assert (indexed_settings.data_dir / "vote_types.json").is_file()
    again = VoteTypes(indexed_settings)  # a restart reads the file instead of rebuilding
    assert again.all() == types
    some = next(iter(types))
    assert vt.annotate([{"division_id": some}, {"title": "no id"}])[0]["vote_type"] == types[some]


def test_plain_english_descriptions_are_added(indexed_settings, tmp_path):
    import shutil

    from hansard.sources.twfy_votes import descriptions

    con = duckdb.connect()
    attach_store(con, indexed_settings.parquet_dir)
    did = con.execute("SELECT division_id FROM divisions LIMIT 1").fetchone()[0]
    raw = indexed_settings.data_dir / "raw" / "twfy_divisions.parquet"
    raw.parent.mkdir(parents=True, exist_ok=True)
    con.execute(f"""COPY (SELECT * FROM (VALUES
        ('{did}', 'commons', 'en', 'Test <i>Bill</i> &#8212; Second Reading', 'MANUAL', 'giving the bill a second reading. ', ''),
        ('x', 'scotland', 'en', 'Elsewhere', 'MANUAL', 'something', '')
      ) t(source_gid, chamber, language, division_title, title_priority, yes_text, no_text)) TO '{raw.as_posix()}'
      (FORMAT PARQUET)""")
    try:
        d = descriptions(raw)
        assert list(d) == [did], "Westminster divisions only"
        assert d[did] == {"title": "Test Bill — Second Reading", "aye_means": "giving the bill a second reading."}
        vt = VoteTypes(indexed_settings)
        assert vt.get(did)["aye_means"] == "giving the bill a second reading."
    finally:
        raw.unlink(missing_ok=True)
        shutil.rmtree(tmp_path, ignore_errors=True)
