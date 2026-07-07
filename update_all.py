"""
update_all.py
Master update pipeline — run this whenever a new semester opens or grades are released.

What it does:
  1. Scrape ALL new semester course lists from CheeseFork (not just the latest)
  2. Pull any new grade data from histogram site
  3. Refresh CheeseFork ratings for all labeled courses
  4. Recompute aggregated CSV from per-semester data
  5. Scrape מלג courses (all semester panels on the ugportal accordion)
  6. Scrape בחירה חופשית courses
  7. Copy updated courses_labeled.csv to ui/public/

Usage:
    python update_all.py              # full update
    python update_all.py --skip-sem   # skip semester scrape (already have JSON)
    python update_all.py --skip-grades # skip grade scrape
    python update_all.py --skip-ratings # skip rating refresh
    python update_all.py --skip-malag # skip מלג scrape
    python update_all.py --skip-free  # skip בחירה חופשית scrape
    python update_all.py --dry-run    # show what would change, don't write
"""

import csv, glob, json, os, re, sys, shutil, time
from datetime import datetime
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

# Force line-buffered stdout so progress is visible immediately even when
# redirected to a file/log (Python fully buffers non-tty stdout by default,
# which otherwise makes a long-running scrape look hung until it exits).
sys.stdout.reconfigure(line_buffering=True)

from scraper_common import (
    CF_BASE, HIST_BASE, CF_API_KEY, CF_PROJECT,
    sem_to_label, discover_semesters, fetch_hist_all_semesters,
    scrape_cf_course, run_pooled,
)

OUTPUT_DIR   = os.path.dirname(os.path.abspath(__file__))
PER_SEM_CSV  = os.path.join(OUTPUT_DIR, "courses_per_semester_all.csv")
AGG_CSV      = os.path.join(OUTPUT_DIR, "courses_aggregated_all.csv")
LABELED_CSV  = os.path.join(OUTPUT_DIR, "courses_labeled.csv")
UI_PUBLIC    = os.path.join(OUTPUT_DIR, "ui", "public", "courses_labeled.csv")

PORTAL_URL   = "https://ugportal.technion.ac.il/הוראה-ובחינות/לימודי-העשרה/"

SKIP_SEM     = "--skip-sem"     in sys.argv
SKIP_GRADES  = "--skip-grades"  in sys.argv
SKIP_RATINGS = "--skip-ratings" in sys.argv
SKIP_MALAG   = "--skip-malag"   in sys.argv
SKIP_FREE    = "--skip-free"    in sys.argv
DRY_RUN      = "--dry-run"      in sys.argv

def log(msg, color=""):
    colors = {"green": "\033[92m", "yellow": "\033[93m", "red": "\033[91m", "blue": "\033[94m", "": ""}
    reset = "\033[0m" if color else ""
    print(f"{colors[color]}[{datetime.now().strftime('%H:%M:%S')}] {msg}{reset}")

# ── CSV helpers ────────────────────────────────────────────────────────────────
def load_per_sem():
    rows = {}
    if not os.path.exists(PER_SEM_CSV): return rows
    with open(PER_SEM_CSV, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            rows[(row["course_id"], row["semester"])] = row
    return rows

def load_agg():
    rows = {}
    if not os.path.exists(AGG_CSV): return rows
    with open(AGG_CSV, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            rows[row["course_id"]] = row
    return rows

def load_labeled():
    rows = {}
    if not os.path.exists(LABELED_CSV): return rows
    with open(LABELED_CSV, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            rows[row["course_id"]] = row
    return rows

def write_per_sem(per_sem):
    cols = ["course_id","course_name","semester","semester_label","students","pass_n","fail_n",
            "pass_pct","min_grade","max_grade","avg_grade","median_grade",
            "avg_general_rank","n_general_rank","hist_url","cf_url"]
    rows = sorted(per_sem.values(), key=lambda r: (r["course_id"], r["semester"]))
    with open(PER_SEM_CSV, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader(); w.writerows(rows)
    return len(rows)

def write_agg(agg):
    cols = ["course_id","course_name","semester_range","n_semesters","avg_final_grade",
            "total_students","avg_pass_pct","avg_general_rank","n_general_rank","hist_url","cf_url"]
    rows = sorted(agg.values(), key=lambda r: r["course_id"])
    with open(AGG_CSV, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader(); w.writerows(rows)
    return len(rows)

def recompute_agg_for(course_id, per_sem, existing_agg):
    hparts = [r for r in per_sem.values()
              if r.get("course_id") == course_id and r.get("avg_grade")]
    if not hparts: return None
    total_n   = sum(float(h["students"]) for h in hparts)
    agg_grade = sum(float(h["avg_grade"]) * float(h["students"]) for h in hparts) / total_n
    agg_pass  = (sum(float(h["pass_pct"]) * float(h["students"]) for h in hparts
                     if h.get("pass_pct")) / total_n) if any(h.get("pass_pct") for h in hparts) else None
    sems = sorted(h["semester"] for h in hparts)
    base = existing_agg.get(course_id, {})
    return {
        "course_id":        course_id,
        "course_name":      base.get("course_name", hparts[0].get("course_name","")),
        "semester_range":   f"{sems[0]} to {sems[-1]}",
        "n_semesters":      len(hparts),
        "avg_final_grade":  f"{agg_grade:.3f}",
        "total_students":   int(total_n),
        "avg_pass_pct":     f"{agg_pass:.3f}" if agg_pass else "",
        "avg_general_rank": base.get("avg_general_rank",""),
        "n_general_rank":   base.get("n_general_rank",""),
        "hist_url":         base.get("hist_url", f"{HIST_BASE}/{course_id}/"),
        "cf_url":           base.get("cf_url",""),
    }

# ── STEP 1: Scrape new semesters ──────────────────────────────────────────────
async def step_semester(browser):
    """
    Discover every semester CheeseFork knows about and backfill any that are
    newer than the latest semester_<code>.json we already have on disk.
    Previously this only ever scraped the single newest semester (max(sems))
    — if two new semesters opened at once (e.g. a skipped summer + the
    following winter), the older of the two would never get scraped on any
    future run either, since it would never be "latest" again.

    Deliberately bounded to "newer than what we already have," not "every
    semester CheeseFork has ever listed" — historical semesters going back to
    2017 were never scraped on purpose (the app only needs current/upcoming
    ones), so a naive "backfill every missing code" would dump ~25 pointless
    historical semester_*.json files into the app's semester dropdown.
    """
    log("STEP 1: Checking for new semesters on CheeseFork", "blue")
    page = await (await browser.new_context()).new_page()

    all_sems = await discover_semesters(page)
    if not all_sems:
        log("  Could not find semester codes", "red")
        await page.close(); return []

    existing_codes = sorted(
        os.path.basename(p).replace("semester_", "").replace(".json", "")
        for p in glob.glob(os.path.join(OUTPUT_DIR, "semester_*.json"))
    )
    latest_known = existing_codes[-1] if existing_codes else None

    missing = [
        s for s in all_sems
        if not os.path.exists(os.path.join(OUTPUT_DIR, f"semester_{s}.json"))
        and (latest_known is None or s > latest_known)
    ]
    if not missing:
        log(f"  No new semesters newer than {latest_known} — nothing to backfill", "yellow")
        await page.close(); return all_sems

    log(f"  Found {len(missing)} new semester(s) to scrape: {missing}", "green")

    for sem in missing:
        url = f"{CF_BASE}?course=all&semester={sem}"
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        try: await page.wait_for_load_state("networkidle", timeout=10000)
        except PlaywrightTimeout: pass

        html  = await page.content()
        hrefs = await page.eval_on_selector_all("a[href]", "els => els.map(e=>e.getAttribute('href')).filter(Boolean)")
        codes = set(re.findall(r"[?&]course=(\d{6,8})\b", html + "\n".join(hrefs)))
        codes = sorted({c.zfill(8) for c in codes})

        out_path = os.path.join(OUTPUT_DIR, f"semester_{sem}.json")
        if not DRY_RUN:
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump({"semester": sem, "courses": codes}, f, ensure_ascii=False, indent=2)
            log(f"  {sem}: saved {len(codes)} courses", "green")
        else:
            log(f"  {sem}: would save {len(codes)} courses (dry run)", "green")

    await page.close()
    return all_sems

# ── STEP 2: Update grades (concurrent) ────────────────────────────────────────
async def step_grades(browser):
    log("STEP 2: Checking for new grade data", "blue")

    per_sem = load_per_sem()
    agg     = load_agg()
    labeled = load_labeled()

    all_jsons = sorted(glob.glob(os.path.join(OUTPUT_DIR, "semester_*.json")))
    all_course_ids = set()
    for path in all_jsons:
        with open(path, encoding="utf-8") as f:
            all_course_ids |= set(json.load(f)["courses"])
    all_course_ids |= {k[0] for k in per_sem.keys()}

    need_check = []
    for cid in sorted(all_course_ids):
        known_sems = {k[1] for k in per_sem if k[0] == cid}
        need_check.append((cid, known_sems))

    log(f"  Checking {len(need_check)} courses for new grades (concurrency=8)...")

    async def worker(page, item):
        cid, known_sems = item
        hist = await fetch_hist_all_semesters(page, cid)
        return cid, known_sems, hist

    def progress(done, total):
        if done % 100 == 0 or done == total:
            log(f"  {done}/{total} courses checked...")

    results = await run_pooled(browser, need_check, worker, concurrency=8, on_progress=progress)

    new_rows, updated_agg = [], set()
    for cid, known_sems, hist in results:
        for sem, r in hist.items():
            if sem in known_sems: continue
            name = labeled.get(cid, {}).get("course_name", "")
            if not name:
                existing = next((v for k, v in per_sem.items() if k[0] == cid), {})
                name = existing.get("course_name", "")

            row = {
                "course_id": cid, "course_name": name, "semester": sem,
                "semester_label": sem_to_label(sem),
                "students":    r["students"],
                "pass_n":      r["pass_n"]      if r["pass_n"]      is not None else "",
                "fail_n":      r["fail_n"]      if r["fail_n"]      is not None else "",
                "pass_pct":    f"{r['pass_pct']:.3f}"    if r["pass_pct"]    is not None else "",
                "min_grade":   f"{r['min_grade']:.3f}"   if r["min_grade"]   is not None else "",
                "max_grade":   f"{r['max_grade']:.3f}"   if r["max_grade"]   is not None else "",
                "avg_grade":   f"{r['avg_grade']:.3f}"   if r["avg_grade"]   is not None else "",
                "median_grade":f"{r['median_grade']:.3f}" if r["median_grade"] is not None else "",
                "avg_general_rank": agg.get(cid, {}).get("avg_general_rank", ""),
                "n_general_rank":   agg.get(cid, {}).get("n_general_rank", ""),
                "hist_url":    f"{HIST_BASE}/{cid}/",
                "cf_url":      f"{CF_BASE}?course={cid}&semester={sem}",
            }
            per_sem[(cid, sem)] = row
            new_rows.append(row)
            updated_agg.add(cid)

    if not new_rows:
        log("  No new grade data found", "yellow")
        return per_sem, agg, set()

    log(f"  Found {len(new_rows)} new grade rows for {len(updated_agg)} courses", "green")

    for cid in updated_agg:
        stats = recompute_agg_for(cid, per_sem, agg)
        if stats: agg[cid] = stats

    if not DRY_RUN:
        n = write_per_sem(per_sem)
        log(f"  Wrote {n} rows to courses_per_semester_all.csv", "green")
        n = write_agg(agg)
        log(f"  Wrote {n} rows to courses_aggregated_all.csv", "green")

    return per_sem, agg, updated_agg

# ── STEP 3: Refresh CheeseFork ratings ────────────────────────────────────────
async def step_ratings(browser, agg):
    log("STEP 3: Refreshing CheeseFork ratings", "blue")

    labeled = load_labeled()
    if not labeled:
        log("  courses_labeled.csv not found — skipping ratings", "yellow")
        return agg

    course_ids = list(labeled.keys())
    log(f"  Fetching ratings for {len(course_ids)} labeled courses...")

    updated = 0
    page = await (await browser.new_context()).new_page()
    try:
        await page.goto(CF_BASE, wait_until="domcontentloaded", timeout=30000)
        try: await page.wait_for_load_state("networkidle", timeout=8000)
        except PlaywrightTimeout: pass
    except Exception:
        pass
    await page.close()

    import urllib.request
    FIRESTORE = f"https://firestore.googleapis.com/v1/projects/{CF_PROJECT}/databases/(default)/documents"

    for i, cid in enumerate(course_ids, 1):
        url = f"{FIRESTORE}/courseFeedback/{cid}?key={CF_API_KEY}"
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                data = json.loads(resp.read())
            fields = data.get("fields", {})
            general_rank = fields.get("generalRank", {})
            if "doubleValue" in general_rank:
                val = general_rank["doubleValue"]
            elif "integerValue" in general_rank:
                val = float(general_rank["integerValue"])
            else:
                continue

            n_field = fields.get("numRatings", fields.get("numberOfRatings", {}))
            n_val = int(n_field.get("integerValue", n_field.get("doubleValue", 0))) if n_field else 0

            old_val = agg.get(cid, {}).get("avg_general_rank", "")
            new_val = f"{val:.3f}"
            if old_val != new_val:
                if cid not in agg:
                    agg[cid] = {"course_id": cid, "course_name": labeled[cid].get("course_name", "")}
                agg[cid]["avg_general_rank"] = new_val
                agg[cid]["n_general_rank"]   = str(n_val)
                updated += 1
        except Exception:
            pass

        if i % 50 == 0:
            log(f"  {i}/{len(course_ids)} rated...")
        time.sleep(0.05)  # gentle rate limit

    log(f"  Updated ratings for {updated} courses", "green" if updated else "yellow")

    if updated and not DRY_RUN:
        n = write_agg(agg)
        log(f"  Wrote {n} rows to courses_aggregated_all.csv", "green")

    return agg

# ── STEP 5: Scrape מלג courses ────────────────────────────────────────────────
async def step_malag(browser, agg, semester_fallback):
    """
    ugportal's מלג listing is now built on a "beefup" accordion (previously
    Divi/et_pb). Each semester has its own <article class="acc-section beefup">
    panel; ALL of them (verified live) render their full course table straight
    into the DOM with no pagination and no need to click/expand — so this
    reads every "רשימת ..." panel directly instead of guessing at the first
    matching toggle.
    """
    log("STEP 5: Scraping מלג courses from Technion portal", "blue")
    labeled = load_labeled()
    existing_rows = [r for r in labeled.values() if r.get("category") != "מלג"]

    page = await (await browser.new_context()).new_page()
    all_courses = []
    try:
        await page.goto(PORTAL_URL, wait_until="domcontentloaded", timeout=30000)
        try: await page.wait_for_load_state("networkidle", timeout=10000)
        except PlaywrightTimeout: pass

        panels = await page.query_selector_all("article.acc-section.beefup")
        if not panels:
            log("  No accordion panels found — portal structure may have changed again", "yellow")
            await page.close(); return existing_rows

        for panel in panels:
            title_el = await panel.query_selector(".accordion-title, .beefup_title")
            title = (await title_el.inner_text()).strip() if title_el else ""
            if "רשימת" not in title:
                continue  # skip exemption ("החרגה...") panels, keep only course lists

            rows = await panel.query_selector_all("table tbody tr")
            panel_courses = []
            for row in rows:
                cells = await row.query_selector_all("td")
                if len(cells) < 2: continue
                texts = [(await c.inner_text()).strip() for c in cells]
                cid_raw = texts[0]
                if not re.match(r"^\d{6,8}$", cid_raw): continue
                panel_courses.append((cid_raw.zfill(8), texts[1] if len(texts) > 1 else ""))
            log(f"  {title[:60]}: {len(panel_courses)} courses")
            all_courses.extend(panel_courses)
    except Exception as e:
        log(f"  מלג scrape error: {e}", "red")
        await page.close(); return existing_rows
    await page.close()

    seen = set()
    unique = [(c, n) for c, n in all_courses if c not in seen and not seen.add(c)]
    log(f"  {len(unique)} unique מלג courses found across all semester panels — fetching credits from CheeseFork...")

    async def worker(page, item):
        cid, _ = item
        return await scrape_cf_course(page, cid, semester_fallback)

    def progress(done, total):
        if done % 10 == 0 or done == total:
            log(f"  {done}/{total} מלג courses checked...")

    scraped = await run_pooled(browser, unique, worker, concurrency=8, on_progress=progress)

    new_rows = []
    for (cid, name_he), (name_scraped, credits, prereqs) in zip(unique, scraped):
        ex = agg.get(cid, {})
        name = ex.get("course_name", "") or name_he
        if not name: name = name_scraped or name_he
        final_credits = credits if credits is not None else 2
        new_rows.append({
            "course_id": cid, "course_name": name, "category": "מלג",
            "credits": final_credits, "prereqs": prereqs,
            "avg_final_grade":  ex.get("avg_final_grade", ""),
            "avg_general_rank": ex.get("avg_general_rank", ""),
            "n_general_rank":   ex.get("n_general_rank", ""),
            "semester": "",
        })

    log(f"  Scraped {len(new_rows)} מלג courses", "green")
    return list(existing_rows) + new_rows

# ── STEP 6: Scrape free choice courses ────────────────────────────────────────
async def step_free_choice(browser, agg, all_rows_so_far, semester_fallback):
    log("STEP 6: Scraping בחירה חופשית courses", "blue")

    malag_sport_ids = {
        r["course_id"] for r in all_rows_so_far
        if r.get("category") in ("מלג", "קורס ספורט")
    }
    existing_rows = [r for r in all_rows_so_far if r.get("category") != "בחירה חופשית"]

    candidates = sorted(
        cid for cid in agg
        if cid.startswith("03")
        and cid not in malag_sport_ids
        and not cid.startswith("0394")
    )
    log(f"  {len(candidates)} candidate 03x courses to check...")

    async def worker(page, cid):
        return await scrape_cf_course(page, cid, semester_fallback)

    def progress(done, total):
        if done % 25 == 0 or done == total:
            log(f"  {done}/{total} candidate courses checked...")

    scraped = await run_pooled(browser, candidates, worker, concurrency=8, on_progress=progress)

    new_rows = []
    for cid, (name_scraped, credits, prereqs) in zip(candidates, scraped):
        ex = agg[cid]
        name = ex.get("course_name", "") or name_scraped
        if credits is None or credits > 2 or prereqs:
            continue
        new_rows.append({
            "course_id": cid, "course_name": name, "category": "בחירה חופשית",
            "credits": credits, "prereqs": "",
            "avg_final_grade":  ex.get("avg_final_grade", ""),
            "avg_general_rank": ex.get("avg_general_rank", ""),
            "n_general_rank":   ex.get("n_general_rank", ""),
            "semester": "",
        })

    log(f"  Found {len(new_rows)} בחירה חופשית courses", "green")
    return existing_rows + new_rows

# ── STEP 4: Update avg_final_grade in courses_labeled.csv ─────────────────────
def step_update_labeled(agg):
    log("STEP 4: Updating courses_labeled.csv with new grades and ratings", "blue")
    labeled = load_labeled()
    if not labeled:
        log("  courses_labeled.csv not found", "red"); return

    updated = 0
    for cid, row in labeled.items():
        agg_row = agg.get(cid, {})
        changed = False
        if agg_row.get("avg_final_grade") and row.get("avg_final_grade") != agg_row["avg_final_grade"]:
            row["avg_final_grade"] = agg_row["avg_final_grade"]
            changed = True
        if agg_row.get("avg_general_rank") and row.get("avg_general_rank") != agg_row["avg_general_rank"]:
            row["avg_general_rank"] = agg_row["avg_general_rank"]
            changed = True
        if changed:
            updated += 1

    if updated == 0:
        log("  Nothing to update in courses_labeled.csv", "yellow"); return

    log(f"  Updating {updated} courses in courses_labeled.csv", "green")
    if not DRY_RUN:
        cols = list(next(iter(labeled.values())).keys())
        with open(LABELED_CSV, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader(); w.writerows(labeled.values())

        if os.path.exists(os.path.dirname(UI_PUBLIC)):
            shutil.copy(LABELED_CSV, UI_PUBLIC)
            log(f"  Copied to {UI_PUBLIC}", "green")
        else:
            log(f"  ui/public not found — copy manually: copy courses_labeled.csv ui\\public\\courses_labeled.csv", "yellow")

# ── Main ──────────────────────────────────────────────────────────────────────
async def main():
    log("=" * 60, "blue")
    log("TECHNION TRACKER — FULL UPDATE PIPELINE", "blue")
    log("=" * 60, "blue")
    if DRY_RUN: log("DRY RUN — no files will be written", "yellow")

    start = time.time()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        all_semesters = []
        if not SKIP_SEM:
            all_semesters = await step_semester(browser)
        else:
            log("STEP 1: Skipping semester scrape (--skip-sem)", "yellow")

        if not all_semesters:
            # Fall back to whatever semester_*.json files already exist on disk
            all_semesters = sorted(
                os.path.basename(path).replace("semester_", "").replace(".json", "")
                for path in glob.glob(os.path.join(OUTPUT_DIR, "semester_*.json"))
            )
        semester_fallback = list(reversed(all_semesters))  # newest first, for course-detail lookups

        per_sem = agg = None
        if not SKIP_GRADES:
            per_sem, agg, updated_courses = await step_grades(browser)
        else:
            log("STEP 2: Skipping grade update (--skip-grades)", "yellow")
            per_sem = load_per_sem()
            agg     = load_agg()

        if not SKIP_RATINGS:
            agg = await step_ratings(browser, agg)
        else:
            log("STEP 3: Skipping ratings refresh (--skip-ratings)", "yellow")

        labeled = load_labeled()
        labeled_rows = list(labeled.values())

        if not SKIP_MALAG:
            labeled_rows = await step_malag(browser, agg, semester_fallback)
        else:
            log("STEP 5: Skipping מלג scrape (--skip-malag)", "yellow")

        if not SKIP_FREE:
            labeled_rows = await step_free_choice(browser, agg, labeled_rows, semester_fallback)
        else:
            log("STEP 6: Skipping בחירה חופשית scrape (--skip-free)", "yellow")

        await browser.close()

    if not DRY_RUN and labeled_rows:
        cols = list(next(iter(load_labeled().values())).keys()) if load_labeled() else \
               ["course_id","course_name","category","credits","prereqs",
                "avg_final_grade","avg_general_rank","n_general_rank","semester"]
        with open(LABELED_CSV, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader(); w.writerows(labeled_rows)
        log(f"  Wrote {len(labeled_rows)} rows to courses_labeled.csv", "green")

    step_update_labeled(agg)

    elapsed = time.time() - start
    log("=" * 60, "blue")
    log(f"DONE in {elapsed:.0f}s", "green")
    log("Next steps:", "blue")
    log("  1. Restart Flask:  python app.py", "")
    log("  2. Reload courses: http://localhost:5000/api/reload", "")

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
