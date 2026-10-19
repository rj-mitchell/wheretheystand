# Where They Stand

**Sourced briefings on what the UK Parliament has said, and who stands where.**

Where They Stand ([wheretheystand.uk](https://wheretheystand.uk)) is an open-source research tool for anyone: journalists, researchers, policy analysts and the public.
The site is open, with no sign-in. It is deliberately
*not* another keyword search; [Hansard's own search](https://hansard.parliament.uk/search) is excellent at finding
every speech that mentions a word. Instead, ask a question such as "What have northern Labour MPs said about
HS2?" and get a short **analytical briefing** that groups the positions taken. For example:

* ministers have been in favour, with examples;
* big-city MPs have been supportive but raised local points, with examples;
* town and rural MPs have ranged from sceptical to hostile, with examples.

Every example is a `[Source n]` citation that opens the verbatim record, with:

* the speaker's name, party, constituency and seat type (city, town or rural) **on that date**;
* their ministerial, shadow or committee-chair role at the time;
* the Hansard column number (or written-question reference);
* links to the official Parliament record and to the exact record on TheyWorkForYou.

The evidence behind each briefing is chosen for balance across speaker groups, not just keyword score. A summary
computed from the data, not written by the model, shows how many contributions, from how many speakers, and from
which groups. If the archive has nothing relevant, the tool says **"No records found."** instead of guessing.

> Where They Stand is an independent project. It is not affiliated with or endorsed by the UK Parliament, and it
> does not use Parliament's logo or the crowned portcullis.

> Status: first public release (beta), covering May 1997 (the 1997 general election) to today: the Commons, the
> Lords (from November 1999), Westminster Hall (from 2000), written answers (from 2001) and statements (from 2002)
> in both Houses, Public Bill Committees, and every division (vote), including the Commons votes of 1997–2001 from
> Public Whip. Also the Scottish Parliament, the Senedd and the Northern Ireland Assembly (from their first
> sittings), oral evidence to select committees (from 2014), early day motions (from 1997), registers of interests
> (the Commons register since 2000), political donations, and election results (general elections from 1997,
> by-elections from 2001).

Besides briefings, the site has free pages built from the record (no AI):

* **MPs and peers**: roles, seats and majorities, what they speak about most, recent contributions, voting record
  and rebellions, registered interests (as they stood on any date since 2000), donations, company roles, all-party
  groups, early day motions, evidence given to committees, and an RSS feed of what they say.
* **Debates** in full, **This week** in Parliament, **Bills** (every sitting and vote), **Charts** of how often a
  subject is raised (by party, House, nation, or across all five UK parliaments), **Compare** and **Timelines**.
* **Every vote explained**: its kind (amendment, second reading, programme motion…) read from the record, with a
  neutral note on what voting either way means, and mySociety's plain-English descriptions where they exist.
* **Early day motions** and who signed them; **Committees**, their reports, the government's responses and who gave
  evidence; the **devolved parliaments** (Scottish Parliament, Senedd, NI Assembly) to read and search.
* **Witnesses** to committees, searchable by keyword and meaning; **Donations** and **Company roles**; **All-party
  groups**; **Topic briefings** kept up to date; and a page of the record's lighter moments.
* A **glossary**, RSS feeds, a documented search **API** (`/developers`), Word and PDF downloads, and embed codes.

Briefings also flag where a speaker had a registered interest (a paid role, a donation, a shareholding) on the day
they spoke that relates to what they said, and offer follow-up questions that build on the briefing.

---

## Contents

1. [How it works](#how-it-works)
2. [Look and feel](#look-and-feel)
3. [Try it on your own computer](#try-it-on-your-own-computer)
4. [Running it on a server](#running-it-on-a-server)
5. [Day-to-day operation](#day-to-day-operation)
6. [Ranking: recency and role weighting](#ranking-recency-and-role-weighting)
7. [How answers stay grounded](#how-answers-stay-grounded)
8. [Data sources and licensing](#data-sources-and-licensing)
9. [Development](#development)
10. [Measured performance](#measured-performance)
11. [Known limitations](#known-limitations)
12. [How it was built](#how-it-was-built)

---

## How it works

```
 TheyWorkForYou scrapedxml ──► streaming parser ──► Parquet (zstd, by source & year) ──► DuckDB SQL
 (rsync or HTTPS, daily)        (lxml iterparse)            │
                                                           ▼
 mysociety/parlparse members/ ──► reference tables ──► speaker-on-date enrichment
 (git pull, daily)                (people, seats, offices,      │
 ONS / mySociety boundaries       regions, parties)             ▼
                                                  LanceDB: BM25 passages + vectors (local model)
                                                           │
 question ─► filter plan (Claude, shown as editable chips) ─► deterministic retrieval & ranking
                                                           │
                                       Claude (Sonnet 5.5) answers ONLY from the sources,
                                       with native search-result citations ─► verification ─► UI
```

* **Ingest.** `lxml.etree.iterparse` streams each file and frees elements as it goes, so memory stays flat
  however large the file is. Ingesting everything since 2010 (24,695 files, 3.7 million records) takes about
  3 minutes with a peak of 664 MB across all worker processes.
* **Storage.** Zstd-compressed Parquet, partitioned by source and year. Each sitting day is replaced atomically
  when Hansard issues a correction. Days older than 90 days are compacted into one file per partition, so
  queries spanning many years run in tens of milliseconds.
* **Republished records.** About 37,000 written answers, mostly corrected Lords answers, are republished in a
  later day's file under the same ID. As on TheyWorkForYou, only the newest copy is searched and cited.
* **Identity.** Speakers are resolved **only by parlparse IDs**, never by name. One person ID covers every
  name and title change. For example, David Cameron is "David Cameron", MP for Witney, Prime Minister in 2012,
  and "Lord Cameron of Chipping Norton", Foreign Secretary in the Lords in 2024. Both are the same person.
* **Temporal joins.** Every open-ended or partial date is normalised: a missing or empty end becomes
  `9999-12-31`, and a year on its own like `"1983"` covers the whole year. Duplicate office rows in the source
  data are removed and overlapping spells merged. Each join is aggregated per record, so a minister with two
  posts never duplicates a speech.
* **Search.** LanceDB holds a BM25 full-text index over 4.7 million passages, plus vectors from a local
  open-source embedding model (`snowflake-arctic-embed-xs`, run on CPU). Both are filtered by date, House,
  region, party, person and role. A Labour filter also includes Labour/Co-operative MPs.
* **Balanced evidence.** The best-ranked candidates (eight per source wanted, at least 150) are grouped into
  government ministers, opposition frontbench, select committee chairs, backbench MPs in city, town and rural
  seats, and backbench peers. Members who raised the subject on three or more separate days among them (not
  counting what they said as ministers) are included first, up to four, so a backbencher who keeps returning to a
  subject is not crowded out; the briefing may say how often they raised it, and the evidence summary lists who
  raised it most. The rest of the evidence set is drawn round-robin across the groups, with at most two
  contributions per speaker and a relevance floor, so a briefing can compare groups instead of echoing one minister
  six times.
* **Seat type.** Every constituency in both boundary sets is classified from official statistics by the size of
  the urban area most of its residents live in, not by city status (`data_static/geo/constituencies.csv`):
  * **city:** the main urban area has 200,000+ people (ONS's "major" built-up area class). Reading, Milton
    Keynes and Bolton count; Bolton because it is part of the Greater Manchester built-up area;
  * **town:** a smaller urban area. Bath, Canterbury and Lichfield count, despite their city status;
  * **rural:** most residents live outside urban areas of 10,000+, the official ONS rural definition. Wells
    and Ely count.

  The urban areas are:
  * England and Wales: ONS's 2011 built-up areas, with 2011 Census populations. A conurbation stops at its
    own county: Bolton is part of Greater Manchester, but towns physically joined to London or another
    conurbation from outside it (Watford, St Albans, Woking, Guildford, Wilmslow) count as towns in their own
    right, using ONS's own sub-division populations;
  * Scotland: National Records of Scotland's Census 2022 settlements, where Greater Glasgow includes Paisley;
  * Northern Ireland: NISRA's 2015 settlements, where Belfast City is the only one of 200,000+.

  Each source's own lookups link small areas to both sets of seats. That gives about 315 city, 225 town and 110 rural seats in each set. Each source label names the urban area and its size. The rules and source URLs are in
  `scripts/build_geo_lookups.py`.

## Look and feel

The web app takes its look from the printed Official Report:
- ivory paper, black serif type (Source Serif 4, bundled), and small-capital headings set between rules;
- column numbers in the margin, and speakers attributed the way Hansard prints them: "Hilary Benn (Leeds Central)
  (Lab)";
- the bound volumes' cloth, green for the Commons and red for the Lords, with gilt lettering. Sources are edged
  in their House's colour, and the sign-in page (invite-only mode) is a volume cover.

The tone is that of a reference work:
- a mark showing the bound record, open, with a ribbon marking the place (`web/public/icon.svg`, with phone icons
  and a 1200×630 link-preview card), and the name in spaced capitals;
- square corners, ruled lists, and example questions numbered as on the Order Paper;
- plain, formal wording; colour only where it carries meaning (the House, a citation, a warning).

Dark mode follows the system setting. The CSS follows [good-css](https://github.com/vojtaholik/good-css):
- logical properties;
- OKLCH colour tokens that hold both the light and dark values;
- one fluid type scale;
- hover styles only where a mouse is present;
- visible focus rings and press feedback;
- animation only for people who have not asked for reduced motion.

## Try it on your own computer

This runs the real site, with real Hansard, on your own Mac or Windows PC at <http://localhost:8000>. Only
your computer can open it. You need:

* [Docker Desktop](https://www.docker.com/products/docker-desktop/) (free for personal use). Install it and start
  it. If its *Settings → Resources* page offers a memory setting, give it at least 8 GB.
* Free disk space: about 5 GB for the most recent year or so, or about 40 GB for everything since 1997.
* An Anthropic API key for the written briefings. Searching works without one.

**1. Get the code.** On GitHub, use the green **Code** button, then **Download ZIP**, and unzip it.

**2. Open a terminal in that folder.**
* Mac: open *Terminal*, type `cd ` (with a space), drag the unzipped folder onto the window, and press Return.
* Windows: open the unzipped folder, right-click an empty space and choose *Open in Terminal*.

**3. Run these commands one at a time.**

```bash
docker compose build
docker compose run --rm app hansard set-key
docker compose run --rm app hansard bootstrap --from 2025-01-01 --embed-minutes 20
docker compose up -d
```

| Command | What it does | Time |
|---|---|---|
| `build` | Builds the app | 5–10 minutes the first time |
| `set-key` | Asks for your API key (it stays hidden as you paste it), checks it with Anthropic and saves it in the app's data folder on your computer | seconds |
| `bootstrap` | Downloads Hansard from 1 January 2025 and member data, builds the search index, and spends up to 20 minutes on semantic matching | about 30 minutes |
| `up -d` | Starts the site in the background | seconds |

Then open <http://localhost:8000>.

* **The whole archive since 1997:** leave out `--from 2025-01-01`. The download is about 9–10 GB and takes
  a few hours; keyword search covers everything straight away.
* **Better semantic matching:** `docker compose run --rm app hansard embed --max-minutes 60` continues it, newest
  first, and can be repeated.
* **Bring in the latest debates:** `docker compose run --rm app hansard sync`. It keeps to the start date you chose.
* **Stop:** `docker compose down`. Your data is kept for next time; `docker compose up -d` starts it again.
* **Remove everything**, including the downloaded data: `docker compose down -v`.

Briefings are charged to your Anthropic account, at roughly 5 US cents each (Claude Sonnet 5.5). The same spending limits as the
public site apply.

**For developers: offline sample.** With Python 3.12+, [uv](https://docs.astral.sh/uv/) and Node 20+, this needs
no downloads and finishes in seconds:

```bash
uv sync --extra embed
(cd web && npm ci && npm run build)
uv run hansard bootstrap --sample     # a small set of real Hansard excerpts, simple offline embedder
uv run hansard serve                  # open http://127.0.0.1:8000
```

## Running it on a server

**Step-by-step guide:** [docs/HOSTING.md](docs/HOSTING.md) covers ordering a server (written for a Hostkey VPS in
London), pointing the domains at it, and running the setup.

Recommended: 4–8 vCPU, 16 GB RAM and 60 GB of disk.

**A smaller server** (for example 2–4 vCPU and 8 GB) works with either or both of:
* recent years only: `hansard bootstrap --from 2019-01-01`. The start date is remembered, so later updates keep to it;
* keyword search only: `hansard bootstrap --keyword-only`. This skips meaning-based search (about 8 GB of vectors
  and a long one-off build). Questions that use different words from the speeches are matched less well.

| Data | Size |
|---|---|
| Raw XML since 2010 | 4.9 GB (roughly 4 GB more for 1997–2010) |
| Parquet store | 1.2 GB |
| Keyword index | 7.9 GB (4.6 GB of passages, 3.3 GB of full-text and filter indices) |
| Vectors | about 8 GB |

Each sync deletes index versions more than an hour old. A large update, such as a big member reshuffle,
can temporarily add several GB until the next sync cleans it up.

On a fresh Ubuntu 24.04 server, with a domain name pointing at it:

```bash
sudo bash deploy/provision.sh
```

The script:
* asks for the main domain name, any other names that should redirect to it (www is added automatically),
  and the Anthropic API key (the key is not shown as you paste it);
* installs Docker;
* starts the app behind Caddy, which provides automatic HTTPS;
* runs the first data load in the background (30–60 minutes);
* installs a timer that runs the sync at 07:15 and 19:15 UK time.

**Semantic-search build time.** The initial embedding of about 4.7 million passages is resumable and runs
newest first. It takes roughly a day on 4 cores, and less on 8. Keyword search works from the start, and
recent years become semantically searchable within the first hours. Each sync continues the embedding for up
to 4 hours.

## Day-to-day operation

| Command | What it does |
|---|---|
| `hansard sync` | Full incremental update (this is what the timer runs): member data, new and corrected Hansard, index, embeddings, compaction, sync log |
| `hansard status` | Files ingested, indexed and embedded; parlparse commit; recent runs |
| `hansard search "…" --from 2018-01-01 --region "North East"` | Ranked sources from the terminal (no model call) |
| `hansard user add/list/disable` | Accounts, only if you switch on invite-only mode (argon2 password hashes) |
| `hansard embed --max-minutes 60` | Continue building semantic vectors |
| `hansard reindex-vectors` | Rebuild the meaning-search index for the current number of vectors, measure each setting against an exact search, keep it only if it finds as much as before, and save the fastest setting |
| `hansard reference` | Rebuild member and role tables from parlparse; the next sync refreshes the changed people in the index |
| `hansard interests`, `devolved`, `evidence`, `edms`, `committees`, `donations`, `groups`, `whips`, `directorships` | Fetch one source now rather than waiting for the twice-daily update (each is also part of `sync`); options such as `interests --archive 500` or `devolved --files 5000` fill in the history in one go. They never run at the same time as an update |
| `hansard backup`, `restore`, `offsite` | Back up the site's own records (saved briefings, reports, usage), restore them, or copy backups off the server |
| `hansard eval-plans`, `eval-sources`, `eval-briefings` | The test set of 62 questions ([docs/EVALUATION.md](docs/EVALUATION.md)) |

**Open access and cost control.** The site is public. Searching costs nothing, but each new briefing is
written by Claude and costs money. So, in `config/settings.toml`, `[usage]`:

* every 10 new briefings in a day, a visitor is shown a short note on the site's costs with the donation link
  (`support_reminder_every`); going through to donate stops it for the day;
* each visitor may have 40 new briefings a day, as a backstop against automated use. Visitors are told apart by a
  salted hash of their IP address; the address itself is never stored;
* each visitor may make 20 requests a minute;
* the whole site has a daily budget ($10) and a monthly budget ($150). When one is reached, briefings pause,
  and source search keeps working;
* identical questions with the same settings are answered from a cache for up to a week, cleared whenever new
  data arrives. Popular questions are paid for once;
* each briefing's token usage and cost is logged to SQLite.

Usage and the sync log are at `/api/admin/usage` and `/api/admin/sync`, with the header
`Authorization: Bearer $HANSARD_ADMIN_TOKEN` (`provision.sh` generates the token). For a private site instead,
set `[auth] required = true` and create accounts with `hansard user add`.

**Shared briefings.** Every completed briefing is saved at `/b/<id>` (ten unguessable characters), with its
sources, checks, the date prepared and the most recent record it drew on. The page carries its own title,
description and a 1200×630 preview card (`/b/<id>/card.png`, drawn with Pillow in the site's typefaces), and is
marked `noindex`. A briefing can be withdrawn with `POST /api/admin/briefings/<id>/withdraw` (admin token); its
page, data and card then return 410 Gone. `GET /api/admin/briefings` lists recent ones.

**Backups.** Everything can be rebuilt from public sources except `data/state.sqlite`, which holds the saved
briefings, reports, usage, the answer cache, feedback, visit counts, the sync log and any accounts.
`hansard backup` makes a checked copy (with `settings.local.toml`) as `data/backups/where-they-stand-DATE.tar.gz`,
keeping the last 14; `hansard restore FILE` puts one back. On a server set up by `provision.sh`, a timer runs it
nightly into `deploy/backups/` (see [docs/HOSTING.md](docs/HOSTING.md#backups)).

## Ranking: recency and role weighting

Ranking is soft. Nothing is excluded unless you set a filter.

```
score = relevance × recency × authority

relevance = ½ · BM25/max(BM25)  +  ½ · calibrated cosine similarity
recency   = 0.35 + 0.65 · 0.5^(age / half-life)           (slider: off … half-life 2 years)
authority = 1 + (W − 1) · m                               (slider W: 1.0× … 2.0×)
```

The authority multiplier `m` depends on the speaker's role on the date of the record:

| Speaker's role on the date | m |
|---|---|
| Government minister or select committee chair whose portfolio matches the question | 1.0 |
| Shadow minister, matching portfolio | 0.6 |
| Minister or chair, other portfolio | 0.5 |
| Shadow minister, other portfolio | 0.3 |
| Ordinary member of a select committee on the question's subject | 0.3 |
| Backbencher | 0 |

* **Recency floor.** Because of the 0.35 floor, a much stronger 2011 match still beats a weak 2025 one.
* **Portfolios.** Departments are mapped to stable portfolios that survive renames. For example DECC → BEIS
  → DESNZ all map to `energy_climate`, so an energy question boosts the energy minister of every era. Every
  post-2010 role title in parlparse maps to a portfolio or is deliberately marked as having none, and a test
  enforces this.
* **Transparency.** The source drawer shows each source's score breakdown.

## How answers stay grounded

1. **Editable filters.** The question becomes an editable filter plan: dates, Houses, regions, parties,
   speakers, roles and boosted portfolios. "Northern MPs" means the North East, North West, and Yorkshire and
   The Humber; you can change this in settings. You see and can edit every filter.
2. **Synthesis rules.** The model is told to synthesise rather than list, to group speakers by the attributes
   given in each source's title (role on the date, party, seat type, region), and to use no other attributes.
   It characterises each group's stance with one to three cited examples, preferably short exact quotations,
   and describes the strength of evidence honestly ("two MPs", "in the records retrieved").
3. **Neutral wording.** The model reports with plain verbs (said, argued, criticised) and may not use loaded or
   judgemental words in its own voice (lied, misled, U-turn, admitted, attacked and the like). Such words can
   appear only inside an exact, cited quotation from the speaker who used them. It never says an individual
   contradicted themselves; it gives what they said on each date. Fair and accurate reports of Parliament have
   legal protection; editorialising would fall outside it.
4. **British English.** Briefings and the one-line interpretation of each question are written in British
   English (spelling, vocabulary, and dates such as 9 October 2026). A final deterministic pass then converts
   any American spelling left in the model's own words (organize, program, percent, defense and so on). It never
   touches quotations, which stay exactly as Hansard records them, nor capitalised names such as the World
   Health Organization.
5. **Deterministic retrieval.** The same plan and sliders always give the same sources.
6. **Sources only.** Claude (Sonnet 5.5) receives only those sources, as search-result blocks with citations
   enabled, under rules that forbid outside knowledge. When the sources don't answer the question, it must say
   "No records found."
7. **Verification.** Every citation is re-checked against our copy of the source text. Any uncited
   substantive sentence is flagged in the UI as unsupported, never passed off as sourced.
8. **No guessing on empty results.** If retrieval finds nothing, the model is not called at all.

## Data sources and licensing

* **Proceedings:** [TheyWorkForYou / ParlParse](https://parser.theyworkforyou.com/hansard.html) XML (mySociety),
  under the Open Parliament Licence v3.0. The Commons division lists of 1997–2001, which those files lack, come
  from [Public Whip](https://www.publicwhip.org.uk/)'s vote matrix (`data_static/votes`).
* **Members, offices and committees:** [mysociety/parlparse](https://github.com/mysociety/parlparse)
  `members/`, comprising `people.json`, `ministers-2010.json` and `posts/westminster-parliament-posts.json`.
* **Early day motions and committees:** the UK Parliament [oral questions and motions](https://oralquestionsandmotions-api.parliament.uk/)
  and [Committees](https://committees-api.parliament.uk/) APIs, under the Open Parliament Licence v3.0.
* **Register of Members' Financial Interests, devolved parliaments and vote descriptions:** TheyWorkForYou's
  published data (`pwdata/scrapedxml/regmem`, `sp-new`, `senedd`, `ni`, `pwdata/votes`), with thanks to mySociety.
* **Election results, members' gender and Parliament IDs:** the UK Parliament
  [Members API](https://members-api.parliament.uk/); general elections of 1997–2005 and by-elections of 2001–2010
  from the House of Commons Library, and by-elections of 1997–2001 from the Commons Information Office's Factsheet
  M16 (`data_static/elections`).
* **Political donations:** the Electoral Commission's register. **Company roles:** Companies House. **Dates of
  birth:** Wikidata. **All-party groups:** the Register of All-Party Parliamentary Groups.
* **Constituency → region:** ONS (Dec 2021 constituency names and codes) and mySociety's
  [2025-constituencies](https://github.com/mysociety/2025-constituencies) data, under the Open Government
  Licence v3.0. The vendored lookup in `data_static/geo/` is rebuilt by `scripts/build_geo_lookups.py`.

The app shows these attributions on every page; [ATTRIBUTION.md](ATTRIBUTION.md) gives every source and its
licence in full.

**Licence.** The code is licensed under the [GNU Affero General Public License v3.0](LICENSE) (AGPL-3.0): you may
use, change and share it, and if you run a changed version as a public website you must offer its source code to
its users under the same licence.
The text of briefings the site writes is licensed under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/); quotations from Hansard remain under the Open
Parliament Licence. The site's [terms](web/terms.html) and [privacy notice](web/privacy.html) give the details.
Hugging Face is used only for a one-off download of the embedding model's weights, which are pinned by
SHA-256. No data comes from there.

## Development

```bash
uv sync --extra embed
uv run pytest                 # Python tests (offline; Claude is mocked)
uv run ruff check src tests scripts
cd web && npm ci && npm test && npm run typecheck
```

* **Python tests.** The fixtures are trimmed copies of real data (`scripts/make_fixtures.py`). They cover:
  * identity across name and title changes (Cameron);
  * role history and duplicate source rows (Benn);
  * concurrent posts (Greening);
  * open-ended and year-only dates;
  * boundary invariance across the 2024 review;
  * every file type in the parser;
  * incremental ingest, including corrections to already-compacted days;
  * memory that stays flat with file size;
  * composite scoring;
  * filters;
  * citation verification;
  * republished written answers (only the newest copy is kept);
  * the "no records found" path, refusals, auth and caps;
  * the daily sync with a member reshuffle, including a sync that fails part-way.
* **Live role-mapping check.** `PARLPARSE_MEMBERS=/path/to/members uv run pytest tests/test_portfolios.py`
  checks the role mapping against the current live data.

## Measured performance

On a 4-vCPU cloud VM, measured when the archive started in 2010 (the record now goes back to 1997):

| Step | Result |
|---|---|
| Download (HTTPS) | 24,695 files, 4.9 GB, 14 minutes |
| Ingest | 3.73 M records, 5,344 divisions, 2.35 M votes; 3 minutes; peak 664 MB across all processes |
| Keyword index | 4.68 M passages in 9 minutes; peak 2.7 GB during the full-text build |
| Daily sync (12 new day-files) | 28 s; peak 2.4 GB |
| Member reshuffle (96 people; 225 k passages updated in place, text and vectors kept) | 21 s; peak 1.7 GB |
| Multi-year SQL (Parquet + DuckDB) | 20–50 ms |
| Search (filters + BM25 + vectors + ranking + full records + speaker profiles) | 0.25–0.5 s warm |
| Embedding (`arctic-embed-xs`, CPU, batch 8) | 20–110 passages/s depending on length and CPU load; peak ~1 GB |

Embedding the whole archive is a one-off backlog of roughly two days on 4 vCPUs. It runs newest first and
resumes where it stopped. Keyword search covers everything in the meantime, and semantic matching covers
whatever has been embedded so far.

## Known limitations

* **Official links.** Hansard's modern XML has no per-speech official URL. For debates, the official link
  opens the sitting day; the column number and the TheyWorkForYou link pinpoint the speech. Written questions
  and statements link to the exact official record.
* **Regions.** Region filters apply to MPs; peers have no constituencies.
* **Party data.** Party on a date comes from parlparse memberships, so the tool reflects any gaps in that
  source.
* **Recency of member data.** Committee and role data reflect what mySociety has published, refreshed daily.
* **Before 2010.** Seats before the 2010 boundaries have no ONS code: they take their region and seat type
  from the later seat of the same name, or their region from `data_static/geo/pre2010_regions.csv` (117 seats
  abolished or renamed; no seat type). Posts of members who left before 2010 come from parlparse's
  `ministers.json`, and their select committee places from a saved copy of Parliament's records
  (`data_static/committees_before_2010.json`). Committee pages start in 2010.
* **Elections before 2010.** Sixteen MPs of 1997–2010 could not
  be matched safely to Parliament's records, so their pages show no election results.
* **Votes of 1997–2001.** Of Public Whip's 1,273 divisions, about 1,165 are matched to the result printed in
  Hansard, which gives them their place in the debate and their kind of vote; the rest keep Public Whip's title and
  link to the day.

## How it was built

Most of Where They Stand was built with the assistance of [Claude Code](https://claude.com/claude-code), Anthropic's
AI coding assistant, using a $100 free credit grant (available at the time of writing) and a $20-a-month Claude
plan. Running the public site is a separate cost: the server, and about 5 US cents for each briefing written.

Many people could build something similar. If there is a public record you think should be easier to search and
understand, this repository is free to learn from and reuse under its [licence](LICENSE).
