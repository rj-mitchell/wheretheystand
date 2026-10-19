import json
from datetime import date

from hansard.reference.build import load_offices


def test_rows_that_end_before_they_start_are_left_out(tmp_path):
    pid = "uk.org.publicwhip/person/25846"
    rows = [
        {"id": "a", "person_id": pid, "organization_id": "hclg", "role": "Chair", "source": "datadotparl/committee",
         "start_date": "2026-09-14", "end_date": "2021-02-22"},
        {"id": "b", "person_id": pid, "organization_id": "hclg", "role": "Chair", "source": "datadotparl/committee",
         "start_date": "2026-09-14"},
    ]
    (tmp_path / "ministers-2010.json").write_text(json.dumps({
        "organizations": [{"id": "hclg", "name": "Housing, Communities and Local Government Committee"}],
        "memberships": rows}))
    offices, stats = load_offices(tmp_path, {}, {pid})
    assert stats["office_rows_impossible"] == 1
    assert [(o["kind"], o["start"], o["end"].year) for o in offices] == [("chair", date(2026, 9, 14), 9999)]
