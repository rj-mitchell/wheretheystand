from datetime import date

import duckdb

from hansard.sources.publicwhip import MATRIX, division_id, load_votes_1997, read_matrix
from hansard.store.parquet import attach_store, write_divisions
from hansard.store.state import StateDB

HEADER = "rowid\tdate\tvoteno\tBill\tmpid1\tmpid3\tmpid4\tmpid5\n"
ROWS = ("11111\t2001-05-10\t209\tInternational Criminal Court Bill [Lords] - New Clause 3\t-9\t 2\t 1\t 4\n"
        "12522\t1997-05-19\t1\tHome Affairs\t4\t 5\t 2\t 3\n")


def _matrix(tmp_path):
    p = tmp_path / "votematrix-1997.dat"
    p.write_text(HEADER + ROWS)
    return p


def test_codes_tellers_and_headings(tmp_path):
    divisions, votes = read_matrix(_matrix(tmp_path))
    icc = next(d for d in divisions if d["number"] == 209)
    assert icc["major_heading"] == "International Criminal Court Bill [Lords]"
    assert icc["minor_heading"] == "New Clause 3"
    assert (icc["ayes"], icc["noes"]) == (1, 1)  # tellers are not counted
    mine = {(v["xml_person_id"], v["vote"], v["teller"]) for v in votes if v["division_id"] == icc["division_id"]}
    assert mine == {("uk.org.publicwhip/member/3", "aye", False), ("uk.org.publicwhip/member/4", "aye", True),
                    ("uk.org.publicwhip/member/5", "no", False)}
    home = next(d for d in divisions if d["number"] == 1)
    assert home["minor_heading"] is None
    assert {v["vote"] for v in votes if v["division_id"] == home["division_id"]} == {"aye", "no", "both"}


def test_loads_once_and_keeps_divisions_already_read_from_hansard(tmp_path):
    root, state = tmp_path / "parquet", StateDB(tmp_path / "state.sqlite")
    hansard = {"division_id": "uk.org.publicwhip/debate/2001-05-10a.349.2", "source_key": "divisions2001-05-10",
               "house": "commons", "date": date(2001, 5, 10), "number": 209, "time": None, "colnum": "349",
               "major_heading": "International Criminal Court Bill [Lords]", "minor_heading": None, "ayes": 220,
               "noes": 74, "official_url": None, "twfy_url": None}
    write_divisions(root, "commons", "divisions2001-05-10", 2001, [hansard], [])
    path = _matrix(tmp_path)
    first = load_votes_1997(root, state, path)
    assert first["loaded"] and first["divisions"] == 1 and first["already_stored"] == 1
    assert load_votes_1997(root, state, path) == {"loaded": False, "reason": "already loaded"}
    con = duckdb.connect()
    attach_store(con, root)
    ids = {r[0] for r in con.execute("SELECT division_id FROM divisions").fetchall()}
    assert ids == {hansard["division_id"], division_id(date(1997, 5, 19), 1)}
    assert con.execute("SELECT count(*) FROM votes").fetchone()[0] == 4


def test_the_saved_matrix_covers_the_1997_parliament():
    divisions, votes = read_matrix(MATRIX)
    assert len(divisions) == 1273
    assert min(d["date"] for d in divisions) == date(1997, 5, 19)
    assert max(d["date"] for d in divisions) == date(2001, 5, 10)
    assert len(votes) > 500_000


def test_divisions_take_their_place_in_the_record_from_the_printed_result():
    from hansard.sources.publicwhip import match_results

    def div(n, ayes, noes):
        return {"division_id": f"pw.0.{n}", "number": n, "ayes": ayes, "noes": noes, "major_heading": "Finance Bill",
                "minor_heading": "Clause 3", "official_url": "https://hansard.parliament.uk/Commons/1999-03-15",
                "twfy_url": "day", "colnum": None}

    def para(rid, ayes, noes):
        return {"record_id": f"uk.org.publicwhip/debate/1999-03-15a.{rid}", "ayes": ayes, "noes": noes,
                "major_heading": "Orders of the Day — Finance Bill", "minor_heading": None, "official_url": None}

    # Public Whip's counts can be a vote or two out; a division whose count matches nothing keeps its own details
    divs = [div(101, 300, 150), div(102, 365, 137), div(103, 10, 20)]
    match_results(divs, [para("801.2", 299, 151), para("842.1", 365, 137)])
    assert [d["division_id"] for d in divs] == ["uk.org.publicwhip/debate/1999-03-15a.801.2",
                                               "uk.org.publicwhip/debate/1999-03-15a.842.1", "pw.0.103"]
    assert divs[1]["colnum"] == "842" and divs[1]["twfy_url"].endswith("?id=1999-03-15a.842.1")
    assert divs[0]["major_heading"] == "Orders of the Day — Finance Bill" and divs[0]["minor_heading"] == "Clause 3"
    # results are taken in order: a later division cannot match an earlier paragraph
    late = [div(1, 365, 137), div(2, 299, 151)]
    match_results(late, [para("801.2", 299, 151), para("842.1", 365, 137)])
    assert late[0]["division_id"].endswith("842.1") and late[1]["division_id"] == "pw.0.2"
