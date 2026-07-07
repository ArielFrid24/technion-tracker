# Scraper pipeline fix — Summer 2026 / Winter 2026-27 backfill

**Date:** 2026-07-07

## Why

Technion opened registration for two new semesters (**202503** = Summer 2026, **202601** = Winter 2026-27). Before running `update_all.py` to pull them in, the scraping code was live-tested against the actual sites (CheeseFork, the histogram site, and the ugportal מלג portal) rather than just re-read — and several real bugs surfaced that would have silently produced wrong data.

## Bugs found (all verified live before fixing)

1. **`step_semester` could only ever backfill the single newest semester** (`latest = max(sems)`). With two new semesters pending at once, it would scrape `202601` and permanently skip `202503` — it would never be "latest" again on any future run either.

2. **The מלג portal (`ugportal.technion.ac.il`) was rebuilt on a different accordion library** (old Divi/`et_pb` → a "beefup" accordion). The scraper's "pick the first toggle matching רשימת/סמסטר" logic was silently reading only the *Spring 2026* panel (10 of 59 courses) and missing Winter 2025 (10), **Winter 2026 — the new semester — (29)**, and Winter 2024 (5). Its container-detection also overshot the new DOM and would have cross-contaminated rows between panels if naively extended. The pagination-click logic was dead code — every panel's full table is already in the DOM with no pagination.

3. **6 separate copies** of the same CheeseFork course-detail scraper (`update_all.py`, `Mendatory_course_scraper.py`, `scrape_malag.py`, `add_science.py`, `Scrape_free_choice.py`, `scraper_for_recommended.py`), each with a hardcoded, rotting semester-fallback tuple. One copy (`scrape_malag.py`) referenced an undefined `BASE` constant — a pre-existing `NameError` bug.

4. **No concurrency anywhere** — the grades re-check loop went through ~1,900+ known courses sequentially, one page load at a time (60-90+ min per run).

## What changed

- **New `scraper_common.py`** — consolidates the 6 duplicated scrape functions into one `scrape_cf_course()`, plus `discover_semesters()` (replaces every hardcoded semester tuple), Firestore/histogram helpers, and `run_pooled()` — a bounded-concurrency worker pool (concurrency=8) built on a page-borrowing `asyncio.Queue`.
- **`update_all.py`**:
  - `step_semester` now backfills every semester *newer than the latest one already on disk* (not just the single newest, and deliberately not a full historical backfill — historical semesters were never scraped on purpose).
  - `step_malag` rewritten against the real DOM (`article.acc-section.beefup` per panel), reads every "רשימת ..." panel and dedupes across all of them, drops the now-pointless click/pagination logic.
  - `step_grades` and `step_free_choice` use `run_pooled` instead of sequential loops.
  - Added line-buffered stdout + periodic progress logging (`N/total checked...`) — a long run redirected to a log file previously showed *nothing* until it exited, which made it impossible to tell a slow-but-healthy run from a hung one.
- **5 standalone legacy scripts** (`Mendatory_course_scraper.py`, `scrape_malag.py`, `add_science.py`, `Scrape_free_choice.py`, `scraper_for_recommended.py`) — swapped their local duplicated scrape function for `scraper_common.scrape_cf_course`, fixing the `scrape_malag.py` `NameError` as a side effect. These aren't part of the recurring `update_all.py` run path.
- **Semester label format** (`app.py`'s `/api/semesters`, `App.js`'s `semLabel`) — shortened from "Winter 2026-2027" to "Winter 26" style everywhere the user sees it.

## Verification

- Isolated test of the new `step_malag` against the live portal: 48 unique courses (up from 10), including the new Winter 2026 course `03230032`.
- `python update_all.py --dry-run`: full 6-step pipeline ran clean end-to-end, correctly found exactly the 2 new semesters (not 26), no writes.
- `python update_all.py` (real run, ~50 min): both `semester_202503.json` (99 courses) and `semester_202601.json` (888 courses) written; `courses_labeled.csv` updated (372 rows) and copied to `ui/public/`.
- End-to-end through Flask: `/api/semesters` lists both new codes, `/api/available` returns non-zero counts for each (28 and 132), `/api/recommend` produces a real schedule for Winter 2026-27.

## Files touched

`scraper_common.py` (new), `update_all.py`, `app.py`, `ui/src/App.js`, `Mendatory_course_scraper.py`, `scrape_malag.py`, `add_science.py`, `Scrape_free_choice.py`, `scraper_for_recommended.py`, plus data files (`semester_202503.json`, `semester_202601.json`, `courses_labeled.csv`, `courses_per_semester_all.csv`, `courses_aggregated_all.csv`, `ui/public/courses_labeled.csv`).
