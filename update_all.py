"""
update_all.py
Master update pipeline — run this whenever a new semester opens or grades are released.

What it does:
  1. Scrape latest semester course list from CheeseFork  (update_latest.py logic)
  2. Pull any new grade data from histogram site         (update_grades.py logic)
  3. Refresh CheeseFork ratings for all labeled courses  (patch_rating.py logic)
  4. Recompute aggregated CSV from per-semester data
  5. Copy updated courses_labeled.csv to ui/public/

Usage:
    python update_all.py              # full update
    python update_all.py --skip-sem   # skip semester scrape (already have JSON)
    python update_all.py --skip-grades # skip grade scrape
    python update_all.py --skip-ratings # skip rating refresh
    python update_all.py --dry-run    # show what would change, don't write
"""

import asyncio, csv, json, os, re, sys, shutil, time
from datetime import datetime
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

OUTPUT_DIR   = os.path.dirname(os.path.abspath(__file__))
PER_SEM_CSV  = os.path.join(OUTPUT_DIR, "courses_per_semester_all.csv")
AGG_CSV      = os.path.join(OUTPUT_DIR, "courses_aggregated_all.csv")
LABELED_CSV  = os.path.join(OUTPUT_DIR, "courses_labeled.csv")
UI_PUBLIC    = os.path.join(OUTPUT_DIR, "ui", "public", "courses_labeled.csv")

CF_BASE      = "https://cheesefork.cf/"
HIST_BASE    = "https://michael-maltsev.github.io/technion-histograms"
CF_API_KEY   = "AIzaSyAfKPyTM83mkLgdQTdx9YS9UXywiswwIYI"
CF_PROJECT   = "cheesefork-de9af"

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

def sem_to_label(sem):
    y, s = int(sem[:4]), sem[4:]
    if s == "01": return f"חורף {y}-{y+1}"
    if s == "02": return f"אביב {y+1}"
    if s == "03": return f"קיץ {y+1}"
    return sem

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

# ── STEP 1: Scrape latest semester ────────────────────────────────────────────
async def step_semester(browser):
    log("STEP 1: Checking for new semester on CheeseFork", "blue")
    page = await (await browser.new_context()).new_page()

    await page.goto(CF_BASE, wait_until="domcontentloaded", timeout=60000)
    try: await page.wait_for_load_state("networkidle", timeout=10000)
    except PlaywrightTimeout: pass

    html = await page.content()
    sems = [s for s in set(re.findall(r'\b(20\d{4})\b', html)) if s[4:] in ("01","02","03")]
    if not sems:
        log("  Could not find semester codes", "red")
        await page.close(); return None

    latest = max(sems)
    out_path = os.path.join(OUTPUT_DIR, f"semester_{latest}.json")

    if os.path.exists(out_path):
        with open(out_path, encoding="utf-8") as f:
            existing = json.load(f)
        log(f"  semester_{latest}.json already exists ({len(existing['courses'])} courses) — skipping", "yellow")
        await page.close(); return latest

    log(f"  New semester found: {latest} — scraping course list...")
    url = f"{CF_BASE}?course=all&semester={latest}"
    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    try: await page.wait_for_load_state("networkidle", timeout=10000)
    except PlaywrightTimeout: pass

    html  = await page.content()
    hrefs = await page.eval_on_selector_all("a[href]", "els => els.map(e=>e.getAttribute('href')).filter(Boolean)")
    codes = set(re.findall(r"[?&]course=(\d{6,8})\b", html + "\n".join(hrefs)))
    codes = sorted({c.zfill(8) for c in codes})

    if not DRY_RUN:
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({"semester": latest, "courses": codes}, f, ensure_ascii=False, indent=2)
    log(f"  Saved {len(codes)} courses for semester {latest}", "green")
    await page.close(); return latest

# ── STEP 2: Update grades ──────────────────────────────────────────────────────
def _parse_finals_table(window):
    tb = re.search(r"<tbody>(.*?)</tbody>", window, re.DOTALL | re.IGNORECASE)
    if not tb: return None
    cells = [re.sub(r"<[^>]+>","",c).strip()
             for c in re.findall(r"<td[^>]*>(.*?)</td>", tb.group(1), re.DOTALL|re.IGNORECASE)]
    if len(cells) < 6: return None
    try:
        students = int(cells[0])
        pf = re.match(r"(\d+)/(\d+)", cells[1])
        avg = float(cells[5])
        if not (0 < students <= 10000 and 0 <= avg <= 100): return None
        return {
            "students": students,
            "pass_n":   int(pf.group(1)) if pf else None,
            "fail_n":   int(pf.group(2)) if pf else None,
            "pass_pct": float(cells[2]) if len(cells) > 2 else None,
            "min_grade":float(cells[3]) if len(cells) > 3 else None,
            "max_grade":float(cells[4]) if len(cells) > 4 else None,
            "avg_grade":avg,
            "median_grade": float(cells[6]) if len(cells) > 6 else None,
        }
    except: return None

def _extract_finals(html, sem):
    m = re.search(rf'id="{re.escape(sem)}-Finals?"', html, re.IGNORECASE)
    if m:
        r = _parse_finals_table(html[m.start():m.start()+2000])
        if r: return r
    parts = []
    for m in re.finditer(rf'id="({re.escape(sem)}-Final_[^"]+)"', html, re.IGNORECASE):
        r = _parse_finals_table(html[m.start():m.start()+2000])
        if r: parts.append(r)
    if not parts: return None
    total_n = sum(p["students"] for p in parts)
    pass_total = sum(p["pass_n"] for p in parts if p["pass_n"] is not None)
    return {
        "students": total_n, "pass_n": pass_total,
        "fail_n":   sum(p["fail_n"] for p in parts if p["fail_n"] is not None),
        "pass_pct": round(100*pass_total/total_n,1) if total_n else None,
        "min_grade":min(p["min_grade"] for p in parts if p["min_grade"] is not None),
        "max_grade":max(p["max_grade"] for p in parts if p["max_grade"] is not None),
        "avg_grade":sum(p["avg_grade"]*p["students"] for p in parts)/total_n,
        "median_grade": None,
    }

async def step_grades(browser, latest_sem):
    log("STEP 2: Checking for new grade data", "blue")

    per_sem = load_per_sem()
    agg     = load_agg()
    labeled = load_labeled()

    # Check all courses in semester JSONs
    import glob
    all_jsons = sorted(glob.glob(os.path.join(OUTPUT_DIR, "semester_*.json")))
    all_course_ids = set()
    for path in all_jsons:
        with open(path, encoding="utf-8") as f:
            all_course_ids |= set(json.load(f)["courses"])
    # Also include already-known courses
    all_course_ids |= {k[0] for k in per_sem.keys()}

    need_check = []
    for cid in sorted(all_course_ids):
        known_sems = {k[1] for k in per_sem if k[0] == cid}
        need_check.append((cid, known_sems))

    log(f"  Checking {len(need_check)} courses for new grades...")

    new_rows     = []
    updated_agg  = set()
    page = await (await browser.new_context()).new_page()

    for i, (cid, known_sems) in enumerate(need_check, 1):
        try:
            await page.goto(f"{HIST_BASE}/{cid}/", wait_until="domcontentloaded", timeout=30000)
            try: await page.wait_for_selector("text=סופי", timeout=5000)
            except PlaywrightTimeout: pass
            await page.wait_for_timeout(500)
            html = await page.content()
        except:
            continue

        all_sems_on_page = sorted(set(re.findall(r'id="(\d{6})-[Ff]inal', html)))
        for sem in all_sems_on_page:
            if sem in known_sems: continue
            r = _extract_finals(html, sem)
            if not r: continue

            name = labeled.get(cid, {}).get("course_name", "")
            if not name:
                existing = next((v for k,v in per_sem.items() if k[0]==cid), {})
                name = existing.get("course_name","")

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
                "avg_general_rank": agg.get(cid,{}).get("avg_general_rank",""),
                "n_general_rank":   agg.get(cid,{}).get("n_general_rank",""),
                "hist_url":    f"{HIST_BASE}/{cid}/",
                "cf_url":      f"https://cheesefork.cf/?course={cid}&semester={sem}",
            }
            per_sem[(cid, sem)] = row
            new_rows.append(row)
            updated_agg.add(cid)

        if i % 50 == 0:
            log(f"  {i}/{len(need_check)} checked, {len(new_rows)} new rows so far...")

    await page.close()

    if not new_rows:
        log("  No new grade data found", "yellow")
        return per_sem, agg, set()

    log(f"  Found {len(new_rows)} new grade rows for {len(updated_agg)} courses", "green")

    # Recompute aggregated stats
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

    # Get Firebase config (needed for Firestore REST)
    try:
        await page.goto(CF_BASE, wait_until="domcontentloaded", timeout=30000)
        try: await page.wait_for_load_state("networkidle", timeout=8000)
        except PlaywrightTimeout: pass
    except: pass

    await page.close()

    # Use Firestore REST API directly
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

            old_val = agg.get(cid, {}).get("avg_general_rank","")
            new_val = f"{val:.3f}"
            if old_val != new_val:
                if cid not in agg:
                    agg[cid] = {"course_id": cid, "course_name": labeled[cid].get("course_name","")}
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

# ── STEP 4: Scrape מלג courses ────────────────────────────────────────────────
PORTAL_URL = "https://ugportal.technion.ac.il/הוראה-ובחינות/לימודי-העשרה/"
CF_BASE    = "https://cheesefork.cf/"

async def scrape_cf_course(page, course_id):
    """Get credits and prereqs from CheeseFork for a single course."""
    for sem in ("202502","202501","202403","202402","202401","202302","202301"):
        try:
            await page.goto(f"{CF_BASE}?course={course_id}&semester={sem}",
                            wait_until="domcontentloaded", timeout=20000)
            try: await page.wait_for_selector("text=נקודות", timeout=3000)
            except PlaywrightTimeout: pass
            try: await page.wait_for_load_state("networkidle", timeout=3000)
            except PlaywrightTimeout: pass

            title = await page.title()
            if course_id.lstrip("0") not in title and course_id not in title:
                continue

            name = ""
            m = re.match(r"^\s*[\d\s]*-\s*(.+?)\s*-", title)
            if m: name = m.group(1).strip()

            body = await page.inner_text("body")
            cm = re.search(r"נקודות[^:]*:\s*(\d+(?:\.\d+)?)", body)
            credits = float(cm.group(1)) if cm and float(cm.group(1)) <= 20 else None

            prereq_str = ""
            pm = re.search(r"מקצועות קדם[:\s]+([\d\u05d0\u05d5\u05d5 \(\)-]+)", body)
            if pm:
                raw = pm.group(1).strip()
                raw = re.sub(r"\u05d5-", "\u05d5 ", raw)
                raw = re.sub(r"\u05d0\u05d5-", "\u05d0\u05d5 ", raw)
                raw = raw.replace("(","").replace(")","")
                parts = []
                for t in raw.split():
                    t = t.strip().rstrip("-")
                    if re.match(r"^\d{6,8}$", t): parts.append(t.zfill(8))
                    elif t == "\u05d0\u05d5": parts.append("OR")
                    elif t == "\u05d5": parts.append("AND")
                prereq_str = " ".join(parts)

            if credits is not None or name:
                return name, credits, prereq_str
        except: continue
    return "", None, ""

async def scrape_malag_table(container):
    courses = []
    rows = await container.query_selector_all("table tbody tr")
    for row in rows:
        cells = await row.query_selector_all("td")
        if len(cells) < 2: continue
        texts = [(await c.inner_text()).strip() for c in cells]
        cid_raw = texts[0]
        if not re.match(r"^\d{6,8}$", cid_raw): continue
        cid = cid_raw.zfill(8)
        name_he = texts[1] if len(texts) > 1 else ""
        courses.append((cid, name_he))
    return courses

async def step_malag(browser, agg):
    log("STEP 5: Scraping מלג courses from Technion portal", "blue")
    labeled = load_labeled()
    existing_rows = [r for r in labeled.values() if r.get("category") != "מלג"]

    page = await (await browser.new_context()).new_page()
    try:
        await page.goto(PORTAL_URL, wait_until="domcontentloaded", timeout=30000)
        try: await page.wait_for_load_state("networkidle", timeout=10000)
        except PlaywrightTimeout: pass

        toggles = await page.query_selector_all(
            ".et_pb_toggle_title, .accordion-title, summary, "
            "[class*='accordion'] h3, [class*='accordion'] h4, "
            "[class*='toggle'] h3, [class*='toggle'] h4"
        )
        target = None
        for t in toggles:
            txt = (await t.inner_text()).strip()
            if "רשימת" in txt or "סמסטר" in txt:
                target = t; break

        if not target:
            log("  Could not find מלג accordion — skipping", "yellow")
            await page.close(); return existing_rows

        container = await target.evaluate_handle("""el => {
            let node = el;
            while (node && node !== document.body) {
                node = node.parentElement;
                if (node.classList && Array.from(node.classList).some(c =>
                    c.includes('toggle') || c.includes('accordion') || c.includes('et_pb')))
                    return node;
            }
            return el.parentElement;
        }""")

        await target.click()
        await page.wait_for_timeout(2000)
        try: await page.wait_for_selector("table tbody tr", timeout=8000)
        except:
            log("  Table not found after click — skipping מלג", "yellow")
            await page.close(); return existing_rows

        all_courses = []
        page_num = 1
        while True:
            courses = await scrape_malag_table(container)
            log(f"  Page {page_num}: {len(courses)} מלג courses")
            all_courses.extend(courses)

            next_btn = None
            nav_links = await container.query_selector_all("a, button")
            for link in nav_links:
                txt = (await link.inner_text()).strip()
                cls = (await link.get_attribute("class") or "")
                if txt in ("›", ">", "Next", "»") or "next" in cls.lower():
                    try:
                        if not await link.is_disabled() and await link.get_attribute("aria-disabled") != "true":
                            next_btn = link; break
                    except: continue
            if not next_btn or page_num >= 10: break
            await next_btn.click(timeout=5000)
            await page.wait_for_timeout(1500)
            page_num += 1

    except Exception as e:
        log(f"  מלג scrape error: {e}", "red")
        await page.close(); return existing_rows

    seen = set()
    unique = [(c,n) for c,n in all_courses if c not in seen and not seen.add(c)]
    log(f"  {len(unique)} unique מלג courses found — fetching credits from CheeseFork...")

    new_rows = []
    cf_page = await (await browser.new_context()).new_page()
    for i, (cid, name_he) in enumerate(unique, 1):
        ex = agg.get(cid, {})
        name = ex.get("course_name","") or name_he
        name_scraped, credits, prereqs = await scrape_cf_course(cf_page, cid)
        if not name: name = name_scraped or name_he
        final_credits = credits if credits is not None else 2
        new_rows.append({
            "course_id": cid, "course_name": name, "category": "מלג",
            "credits": final_credits, "prereqs": prereqs,
            "avg_final_grade":  ex.get("avg_final_grade",""),
            "avg_general_rank": ex.get("avg_general_rank",""),
            "n_general_rank":   ex.get("n_general_rank",""),
            "semester": "",
        })
        if i % 10 == 0: log(f"  {i}/{len(unique)} מלג processed...")
    await cf_page.close()
    await page.close()

    log(f"  Scraped {len(new_rows)} מלג courses", "green")
    return list(existing_rows) + new_rows

# ── STEP 6: Scrape free choice courses ────────────────────────────────────────
async def step_free_choice(browser, agg, all_rows_so_far):
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

    new_rows = []
    page = await (await browser.new_context()).new_page()
    for i, cid in enumerate(candidates, 1):
        ex = agg[cid]
        name_scraped, credits, prereqs = await scrape_cf_course(page, cid)
        name = ex.get("course_name","") or name_scraped
        if credits is None or credits > 2 or prereqs:
            continue
        new_rows.append({
            "course_id": cid, "course_name": name, "category": "בחירה חופשית",
            "credits": credits, "prereqs": "",
            "avg_final_grade":  ex.get("avg_final_grade",""),
            "avg_general_rank": ex.get("avg_general_rank",""),
            "n_general_rank":   ex.get("n_general_rank",""),
            "semester": "",
        })
        if i % 20 == 0: log(f"  {i}/{len(candidates)} checked, {len(new_rows)} kept...")
    await page.close()

    log(f"  Found {len(new_rows)} בחירה חופשית courses", "green")
    return existing_rows + new_rows

# ── STEP 7: Update avg_final_grade in courses_labeled.csv ─────────────────────

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

        # Copy to UI public folder
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

        latest_sem = None
        if not SKIP_SEM:
            latest_sem = await step_semester(browser)
        else:
            log("STEP 1: Skipping semester scrape (--skip-sem)", "yellow")

        per_sem = agg = None
        if not SKIP_GRADES:
            per_sem, agg, updated_courses = await step_grades(browser, latest_sem)
        else:
            log("STEP 2: Skipping grade update (--skip-grades)", "yellow")
            per_sem = load_per_sem()
            agg     = load_agg()

        if not SKIP_RATINGS:
            agg = await step_ratings(browser, agg)
        else:
            log("STEP 3: Skipping ratings refresh (--skip-ratings)", "yellow")

        # Steps 5 & 6: malag + free choice
        labeled = load_labeled()
        labeled_rows = list(labeled.values())

        if not SKIP_MALAG:
            labeled_rows = await step_malag(browser, agg)
        else:
            log("STEP 5: Skipping מלג scrape (--skip-malag)", "yellow")

        if not SKIP_FREE:
            labeled_rows = await step_free_choice(browser, agg, labeled_rows)
        else:
            log("STEP 6: Skipping בחירה חופשית scrape (--skip-free)", "yellow")

    # Write updated labeled CSV
    if not DRY_RUN and labeled_rows:
        cols = list(next(iter(load_labeled().values())).keys()) if load_labeled() else                ["course_id","course_name","category","credits","prereqs",
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

asyncio.run(main())