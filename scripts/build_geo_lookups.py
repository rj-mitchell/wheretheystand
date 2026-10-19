# /// script
# requires-python = ">=3.12"
# dependencies = ["duckdb==1.5.6", "openpyxl==3.1.5", "pyarrow>=17"]
# ///
"""Build data_static/geo/constituencies.csv: every Westminster seat in both boundary
sets, with its region and seat type (city, town or rural) from official statistics.

Usage:
    git clone --depth 1 --filter=blob:none --sparse \
        https://github.com/mysociety/2025-constituencies /tmp/2025-constituencies
    git -C /tmp/2025-constituencies sparse-checkout set data/packages data/raw/external
    uv run scripts/build_geo_lookups.py /tmp/2025-constituencies [download-cache-dir]

Boundary sets and regions (mySociety's 2025-constituencies, from ONS and the
Boundary Commissions):

* 2010 set (ONS "Westminster Parliamentary Constituencies (Dec 2021)", the
  2010-2024 boundaries): valid 2010-05-06 .. 2024-07-03 (Scotland's 59 seats were
  first used in 2005; the archive starts in 2010 so 2010-05-06 is used).
* 2024 set: valid 2024-07-04 .. 9999-12-31.
* Regions for 2010-set seats come from the 2024 seats they overlap; the script
  asserts each 2010 seat overlaps seats in one region only.

Seat type is decided by the size of the official urban area most of a seat's
residents live in, not by city status:

* ``rural`` - under half the seat's residents live in urban areas of 10,000+
  (the ONS rural definition: rural OAs are exactly those outside 2011 built-up
  areas of 10,000+);
* ``city``  - otherwise, if the urban area holding most of its urban residents
  has 200,000+ people (ONS's "major" built-up area class);
* ``town``  - otherwise.

Official urban areas, small-area populations and lookups (all best-fit by
population-weighted centroid, as published):

* England and Wales - ONS 2011 Built-up Areas with 2011 Census output-area
  populations (KS101EW) and ONS output-area lookups to both boundary sets. A
  conurbation stops at its county: in a built-up area that is mostly London
  boroughs or metropolitan districts (Greater London, Greater Manchester, West
  Midlands, ...), each ONS sub-division outside them (Watford, St Albans, Woking,
  Wilmslow, ...) is a town in its own right. Bolton stays in Greater Manchester.
* Scotland - National Records of Scotland Census 2022 settlements (Greater
  Glasgow includes Paisley and Clydebank), with Census 2022 output-area
  populations and NRS lookups to both boundary sets.
* Northern Ireland - NISRA's 2015 settlement classification (bands A-D are the
  settlements of 10,000+; band A is Belfast City), with Census 2021 data-zone
  populations and NISRA lookups to both boundary sets.

Each urban area's population is the sum of its small areas (for England and Wales
this equals the official Nomis built-up-area figures exactly).

Sources: ONS, NRS and NISRA, Open Government Licence v3.0; mySociety (CC BY 4.0).
"""

from __future__ import annotations

import csv
import http.client
import io
import os
import ssl
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

import duckdb
import pyarrow as pa

ITL1 = {
    "Eastern": "East of England",
    "Yorkshire and the Humber": "Yorkshire and The Humber",
}

OUT = Path(__file__).resolve().parents[1] / "data_static" / "geo" / "constituencies.csv"

URBAN_POPULATION = 10_000
CITY_POPULATION = 200_000

GEOPORTAL = "https://geoportal.statistics.gov.uk/api/download/v1/items/{}/csv?layers=0"
SOURCES = {
    # England and Wales (ONS)
    "ew_oa_bua": GEOPORTAL.format("df834d6d654d4f4a9a211fdbbb8893bd"),    # OA11 -> BUASD11 -> BUA11 (Dec 2011)
    "ew_oa_pcon10": GEOPORTAL.format("969687988afe4606815471980042b6fd"),  # OA11 -> PCON11 (2010-2024 seats)
    "ew_oa_pcon24": GEOPORTAL.format("4491820e4f274755a2553c42fc6a250c"),  # OA11 -> PCON24
    "ew_oa_pop": "https://www.nomisweb.co.uk/output/census/2011/ks101ew_2011_oa.zip",  # KS101EW
    # Scotland (NRS)
    "sc_index": "https://www.nrscotland.gov.uk/media/utrbt5ze/census_2022_index.zip",  # OA22 -> settlement, 2005 seat
    "sc_oa_pop": "https://www.nrscotland.gov.uk/media/owpknvgk/outputarea2022_usualresidentpopulation.csv",
    "sc_oa_pcon24": "https://www.nrscotland.gov.uk/media/njkmhppf/oa22_ukpc24.zip",
    # Northern Ireland (NISRA)
    "ni_dz": "https://www.nisra.gov.uk/files/nisra/documents/2025-04/geography-data-zone-and-super-data-zone-lookups-v3.xlsx",
    "ni_pop": "https://www.nisra.gov.uk/system/files/statistics/census-2021-ms-a01.xlsx",
}


# ---- downloads ---------------------------------------------------------------------------

def fetch(cache: Path, key: str) -> Path:
    url = SOURCES[key]
    name = url.split("?")[0].rsplit("/", 1)[-1]
    dest = cache / key / (name if "." in name else f"{name}.csv")
    if dest.exists() and dest.stat().st_size:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    ctx = ssl.create_default_context(cafile=os.environ.get("SSL_CERT_FILE"))
    for attempt in range(8):
        if attempt:
            time.sleep(5 * attempt)
        req = urllib.request.Request(url, headers={"User-Agent": "hansardgpt-geo-build"})
        try:
            with urllib.request.urlopen(req, timeout=300, context=ctx) as resp:
                if resp.status == 202:  # the geoportal builds the file on first request
                    continue
                data = resp.read()
        except (OSError, http.client.HTTPException) as exc:  # includes timeouts and 5xx responses
            print(f"  {key}: {exc}; retrying")
            continue
        expect_zip = dest.suffix in (".zip", ".xlsx")
        if expect_zip != data.startswith(b"PK") or data.lstrip()[:1] in (b"<", b"{"):
            raise SystemExit(f"{url} returned something other than the expected {dest.suffix} file")
        tmp = dest.with_suffix(dest.suffix + ".part")
        tmp.write_bytes(data)
        tmp.replace(dest)
        print(f"  downloaded {key}: {len(data) / 1e6:.1f} MB")
        return dest
    raise SystemExit(f"{url} could not be downloaded after several attempts; try again later")


def zip_member(path: Path, suffix: str, encoding: str = "utf-8-sig") -> str:
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if n.replace("\\", "/").endswith(suffix)]
        if len(names) != 1:
            raise SystemExit(f"{path.name}: expected one member ending {suffix!r}, found {names}")
        return z.read(names[0]).decode(encoding)


def csv_table(text: str, columns: dict[str, str]) -> pa.Table:
    """Read selected CSV columns (source name -> output name) as strings."""
    reader = csv.DictReader(io.StringIO(text))
    reader.fieldnames = [f.strip().lstrip("﻿") for f in reader.fieldnames or []]
    missing = [c for c in columns if c not in reader.fieldnames]
    if missing:
        raise SystemExit(f"columns {missing} not found (have {reader.fieldnames[:12]}); has the source changed?")
    rows = list(reader)
    return pa.table({out: [(r.get(src) or "").strip() or None for r in rows] for src, out in columns.items()})


def sheet_table(path: Path, sheet: str, header: str, columns: dict[str, str]) -> pa.Table:
    """Rows of an .xlsx sheet below the row whose first cell is ``header``."""
    import openpyxl

    ws = openpyxl.load_workbook(path, read_only=True)[sheet]
    rows = iter(ws.iter_rows(values_only=True))
    head = next(r for r in rows if r and r[0] == header)
    idx = {name: head.index(name) for name in columns}
    data = [r for r in rows if r and r[0] is not None]
    return pa.table({out: [None if r[idx[src]] is None else str(r[idx[src]]).strip() for r in data]
                     for src, out in columns.items()})


# ---- small areas, one table per nation ------------------------------------------------------
# Each yields rows (area, pop, ua_code, ua_name, seat2010, seat2024).

def england_wales(con: duckdb.DuckDBPyConnection, cache: Path) -> None:
    con.register("ew_bua", csv_table(fetch(cache, "ew_oa_bua").read_text(encoding="utf-8-sig"), {
        "OA11CD": "area", "BUA11CD": "bua", "BUA11NM": "bua_name", "BUASD11CD": "sd", "BUASD11NM": "sd_name",
        "LAD11CD": "lad", "LAD11NM": "lad_name"}))
    con.register("ew_p10", csv_table(fetch(cache, "ew_oa_pcon10").read_text(encoding="utf-8-sig"),
                                     {"OA11CD": "area", "PCON11CD": "seat"}))
    con.register("ew_p24", csv_table(fetch(cache, "ew_oa_pcon24").read_text(encoding="utf-8-sig"),
                                     {"OA11CD": "area", "PCON24CD": "seat"}))
    con.register("ew_pop", csv_table(zip_member(fetch(cache, "ew_oa_pop"), "KS101EWDATA.CSV"),
                                     {"GeographyCode": "area", "KS101EW0001": "pop"}))
    con.execute("""
        CREATE TABLE ew_oa AS
        SELECT b.*, CAST(p.pop AS INTEGER) pop, s10.seat seat2010, s24.seat seat2024,
               b.lad LIKE 'E08%' OR b.lad LIKE 'E09%' AS core  -- London boroughs and metropolitan districts
        FROM ew_bua b JOIN ew_pop p USING (area) JOIN ew_p10 s10 USING (area) JOIN ew_p24 s24 USING (area)
        WHERE b.area LIKE 'E00%' OR b.area LIKE 'W00%'""")
    # A conurbation stops at its county: in a built-up area that is mostly London or metropolitan
    # districts, the sub-divisions outside them (Watford, St Albans, Woking, ...) are towns of their own.
    con.execute("""
        CREATE TABLE ew AS
        WITH metro AS (SELECT bua FROM ew_oa WHERE bua IS NOT NULL GROUP BY bua
                       HAVING sum(CASE WHEN core THEN pop ELSE 0 END) > 0.5 * sum(pop)),
             straddling AS (  -- sub-divisions partly inside a borough or metropolitan district
                SELECT sd, sum(CASE WHEN NOT core THEN pop ELSE 0 END) >= 0.5 * sum(pop) AS mostly_outside
                FROM ew_oa WHERE sd IS NOT NULL GROUP BY sd HAVING bool_or(core)),
             units AS (
                SELECT o.*, (m.bua IS NOT NULL AND NOT o.core AND o.sd IS NOT NULL) AS split,
                       st.sd IS NOT NULL AS straddles, coalesce(st.mostly_outside, true) AS mostly_outside
                FROM ew_oa o LEFT JOIN metro m USING (bua) LEFT JOIN straddling st USING (sd))
        SELECT area, pop, seat2010, seat2024, NULL::VARCHAR band,
               CASE WHEN split THEN sd || '/' || CASE WHEN straddles THEN lad ELSE '' END ELSE bua END ua_code,
               CASE WHEN split AND NOT mostly_outside THEN lad_name  -- e.g. the Dartford end of "Bexley"
                    WHEN split THEN regexp_replace(sd_name, ' BUASD$', '')
                    ELSE regexp_replace(bua_name, ' BUA$', '') END ua_name
        FROM units""")
    # drop ONS's disambiguating "(Place)" only where the shorter name stays unique
    con.execute("""
        CREATE OR REPLACE TABLE ew AS
        WITH n AS (SELECT ua_code, any_value(ua_name) full_name,
                          regexp_replace(any_value(ua_name), ' \\(.*\\)$', '') short_name
                   FROM ew WHERE ua_code IS NOT NULL GROUP BY ua_code),
             dup AS (SELECT short_name FROM n GROUP BY short_name HAVING count(*) > 1)
        SELECT ew.* REPLACE (CASE WHEN dup.short_name IS NULL THEN n.short_name ELSE n.full_name END AS ua_name)
        FROM ew LEFT JOIN n USING (ua_code) LEFT JOIN dup ON dup.short_name = n.short_name""")
    n = con.execute("SELECT count(*), sum(pop) FROM ew").fetchone()
    if n != (181_408, 56_075_912):
        raise SystemExit(f"England and Wales: expected 181,408 output areas and 56,075,912 people, got {n}")


def scotland(con: duckdb.DuckDBPyConnection, cache: Path) -> None:
    index = fetch(cache, "sc_index")
    con.register("sc_idx", csv_table(zip_member(index, "OA_TO_HIGHER_AREAS.csv"),
                                     {"OA2022": "area", "CSETT2022": "ua_code", "UKPC2005": "seat"}))
    con.register("sc_names", csv_table(zip_member(index, "Census Settlement 2022 Lookup.csv", "cp1252"),
                                       {"CensusSettlement2022Code": "ua_code", "CensusSettlement2022Name": "ua_name"}))
    con.register("sc_pop", csv_table(fetch(cache, "sc_oa_pop").read_text(encoding="utf-8-sig"),
                                     {"OutputArea2022": "area", "UsualResidentPopulation": "pop"}))
    con.register("sc_p24", csv_table(zip_member(fetch(cache, "sc_oa_pcon24"), "OA22_UKPC24.csv"),
                                     {"OA22": "area", "UKPC24": "seat"}))
    con.execute("""
        CREATE TABLE sc AS
        SELECT i.area, CAST(p.pop AS INTEGER) pop, i.ua_code, n.ua_name, i.seat seat2010, s24.seat seat2024,
               NULL::VARCHAR band
        FROM sc_idx i JOIN sc_pop p USING (area) JOIN sc_p24 s24 USING (area)
        LEFT JOIN sc_names n USING (ua_code)""")
    n = con.execute("SELECT count(*), sum(pop), count(ua_code) - count(ua_name) FROM sc").fetchone()
    if n != (46_363, 5_440_284, 0):
        raise SystemExit(f"Scotland: expected 46,363 output areas, 5,440,284 people, all settlements named; got {n}")


def northern_ireland(con: duckdb.DuckDBPyConnection, cache: Path) -> None:
    con.register("ni_dz", sheet_table(fetch(cache, "ni_dz"), "DZ2021_Admin_geog_lookup", "DZ2021_code", {
        "DZ2021_code": "area", "PARLCON2008_code": "seat2010", "PARLCON2024_code": "seat2024",
        "SETTLEMENT2015_code": "ua_code", "SETTLEMENT2015_name": "ua_name", "SETTLEMENT2015_Band": "band"}))
    con.register("ni_pop", sheet_table(fetch(cache, "ni_pop"), "DZ", "Geography", {
        "Geography Code": "area", "All usual residents": "pop"}))
    con.execute("""
        CREATE TABLE ni AS
        SELECT d.area, CAST(p.pop AS INTEGER) pop, d.ua_code,
               regexp_replace(d.ua_name, '\\s*(\\(.*|including .*)$', '', 'i') ua_name, d.seat2010, d.seat2024,
               d.band
        FROM ni_dz d JOIN ni_pop p USING (area)""")
    con.create_function("title", lambda v: v.title() if v else v, ["VARCHAR"], "VARCHAR")
    con.execute("UPDATE ni SET ua_name = title(lower(ua_name))")  # NISRA names are in capitals
    n = con.execute("SELECT count(*), sum(pop) FROM ni").fetchone()
    if n[0] != 3_780 or not 1_900_000 < n[1] < 1_910_000:
        raise SystemExit(f"Northern Ireland: expected 3,780 data zones and ~1.9m people, got {n}")


def classify(con: duckdb.DuckDBPyConnection, seat_col: str) -> dict[str, tuple]:
    """seat -> (settlement, urban area name, urban area population, urban share)."""
    rows = con.execute(f"""
        WITH areas AS (
            SELECT area, pop, ua_code, ua_name, band, {seat_col} seat FROM ew
            UNION ALL SELECT area, pop, ua_code, ua_name, band, {seat_col} FROM sc
            UNION ALL SELECT area, pop, ua_code, ua_name, band, {seat_col} FROM ni),
        ua AS (SELECT ua_code, any_value(ua_name) ua_name, sum(pop) ua_pop, any_value(band) band
               FROM areas WHERE ua_code IS NOT NULL GROUP BY ua_code),
        -- NISRA's bands are the official size classes (A-D: 10,000+); elsewhere the population decides
        a AS (SELECT areas.seat, areas.pop, ua.ua_code, ua.ua_name, ua.ua_pop,
                     coalesce(CASE WHEN ua.band IS NOT NULL THEN ua.band IN ('A', 'B', 'C', 'D')
                                   ELSE ua.ua_pop >= {URBAN_POPULATION} END, false) urban
              FROM areas LEFT JOIN ua USING (ua_code)),
        by_ua AS (SELECT seat, ua_code, any_value(ua_name) ua_name, any_value(ua_pop) ua_pop, sum(pop) p
                  FROM a WHERE urban GROUP BY seat, ua_code)
        SELECT t.seat, t.total, t.urban_pop, m.ua_name, m.ua_pop
        FROM (SELECT seat, sum(pop) total, sum(CASE WHEN urban THEN pop ELSE 0 END) urban_pop
              FROM a GROUP BY seat) t
        LEFT JOIN (SELECT seat, ua_name, ua_pop FROM by_ua
                   QUALIFY row_number() OVER (PARTITION BY seat ORDER BY p DESC, ua_pop DESC, ua_code) = 1) m
          USING (seat)""").fetchall()
    out = {}
    for seat, total, urban_pop, ua_name, ua_pop in rows:
        share = urban_pop / total
        kind = "rural" if share < 0.5 else ("city" if ua_pop >= CITY_POPULATION else "town")
        out[seat] = (kind, ua_name, ua_pop, round(share, 4))
    return out


def main(repo: Path, cache: Path) -> None:
    pkg = repo / "data" / "packages"
    c25 = pkg / "parliament_con_2025" / "parl_constituencies_2025.parquet"
    ov = pkg / "geographic_overlaps" / "PARL10_PARL25_combo_overlap.parquet"
    c21 = repo / "data" / "raw" / "external" / "Westminster_Parliamentary_Constituencies_(Dec_2021)_UK_BFC.parquet"
    lad_file = next((repo / "data" / "raw" / "external").glob("Local_Authority_Districts_May_2023*.parquet"), None)
    if lad_file is None:
        raise SystemExit("mySociety checkout lacks data/raw/external/Local_Authority_Districts_May_2023*.parquet")
    con = duckdb.connect()
    con.execute(f"create view c25 as select short_code, gss_code, name as cname, nation, region from '{c25}'")
    con.execute(f"create view ov as select * from '{ov}'")
    con.execute(f"create view c21 as select PCON21CD as gss, PCON21NM as cname from '{c21}'")
    con.execute(f"create view lad as select LAD23CD, LAD23NM from '{lad_file}'")

    england_wales(con, cache)
    scotland(con, cache)
    northern_ireland(con, cache)
    kinds = {"2010": classify(con, "seat2010"), "2024": classify(con, "seat2024")}

    # density and main council, from mySociety's population-weighted overlaps (for reference)
    basics = {}
    for seat_col, table in (("PARL10", "PARL10_LAD23"), ("PARL25", "PARL25_LAD23")):
        rows = con.execute(f"""
            select o.{seat_col},
                   any_value(o.original_pop) / (any_value(o.original_area) / 1e6) as density,
                   arg_max(l.LAD23NM, o.overlap_pop) as main_area
            from '{pkg}/geographic_overlaps/{table}_combo_overlap.parquet' o
            join lad l on l.LAD23CD = o.LAD23 group by 1""").fetchall()
        for seat, density, main_area in rows:
            basics[(seat_col, seat)] = (round(density), main_area)

    spans = con.execute(
        """select o.PARL10, list(distinct c.region) from ov o join c25 c on c.short_code = o.PARL25
           where o.percentage_overlap_area > 0.01 group by 1 having len(list(distinct c.region)) <> 1"""
    ).fetchall()
    if spans:
        raise SystemExit(f"2010 seats overlapping several regions: {spans[:5]}")

    old = con.execute(
        """with best as (
             select o.PARL10 gss, arg_max(c.region, o.percentage_overlap_area) region,
                    arg_max(c.nation, o.percentage_overlap_area) nation
             from ov o join c25 c on c.short_code = o.PARL25 group by 1)
           select c21.gss, c21.cname, best.region, best.nation, c21.gss
           from c21 join best on best.gss = c21.gss order by c21.cname"""
    ).fetchall()
    new = con.execute("select gss_code, cname, region, nation, short_code from c25 order by cname").fetchall()
    if len(old) != 650 or len(new) != 650:
        raise SystemExit(f"expected 650 + 650 seats, got {len(old)} + {len(new)}")
    for boundary, rows in (("2010", old), ("2024", new)):
        missing = sorted({r[0] for r in rows} - kinds[boundary].keys())
        if missing:
            raise SystemExit(f"{boundary} seats with no official small areas: {missing[:10]}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    counts: dict[tuple[str, str], int] = {}
    with open(OUT, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["gss_code", "name", "region", "nation", "boundary_set", "valid_from", "valid_to",
                    "settlement", "density_per_km2", "main_area", "urban_area", "urban_area_pop", "urban_share"])
        for boundary, rows, col, start, end in (("2010", old, "PARL10", "2010-05-06", "2024-07-03"),
                                                ("2024", new, "PARL25", "2024-07-04", "9999-12-31")):
            for gss, name, region, nation, key in rows:
                density, main_area = basics[(col, key)]
                kind, area, area_pop, share = kinds[boundary][gss]
                counts[(boundary, kind)] = counts.get((boundary, kind), 0) + 1
                w.writerow([gss, name, ITL1.get(region, region), nation, boundary, start, end, kind, density,
                            main_area, area or "", area_pop or "", share])
    print(f"wrote {OUT} ({len(old)} + {len(new)} seats): {dict(sorted(counts.items()))}")


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        raise SystemExit(__doc__)
    main(Path(sys.argv[1]), Path(sys.argv[2]) if len(sys.argv) == 3 else Path.home() / ".cache" / "hansard-geo")
