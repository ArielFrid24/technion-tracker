"""
update_grades.py
Step 2: For each course in the latest semester_<CODE>.json, check if new
        grade data has appeared on the histogram site and update the main CSVs.

Also updates avg_final_grade in courses_aggregated_all.csv for any course
that gets new semester data.

Usage:
    python update_grades.py                  # uses latest semester_*.json found
    python update_grades.py 202502           # specify semester explicitly
    python update_grades.py --all            # check all courses in all semester JSONs
"""
import asyncio, csv, json, os, re, sys
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

OUTPUT_DIR  = os.path.dirname(os.path.abspath(__file__))
HIST_BASE   = "https://michael-maltsev.github.io/technion-histograms"
PER_SEM_CSV = os.path.join(OUTPUT_DIR, "courses_per_semester_all.csv")
AGG_CSV     = os.path.join(OUTPUT_DIR, "courses_aggregated_all.csv")

def sem_to_label(sem):
    year, s = int(sem[:4]), sem[4:]
    if s == "01": return f"חורף {year}-{year+1}"
    if s == "02": return f"אביב {year}"
    if s == "03": return f"קיץ {year}"
    return sem

def fmt(v, d=3):
    return f"{v:.{d}f}" if v is not None else ""

# ── CSV loaders ────────────────────────────────────────────────────────────────
def load_per_sem():
    """Returns dict: (course_id, semester) -> row"""
    rows = {}
    if not os.path.exists(PER_SEM_CSV):
        return rows
    with open(PER_SEM_CSV, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            rows[(row["course_id"], row["semester"])] = row
    return rows

def load_agg():
    """Returns dict: course_id -> row"""
    rows = {}
    if not os.path.exists(AGG_CSV):
        return rows
    with open(AGG_CSV, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            rows[row["course_id"]] = row
    return rows

# ── Histogram scraper (from cheesefork_scraper.py) ────────────────────────────
def _parse_finals_table(window):
    tb = re.search(r"<tbody>(.*?)</tbody>", window, re.DOTALL | re.IGNORECASE)
    if not tb: return None
    cells = [re.sub(r"<[^>]+>", "", c).strip()
             for c in re.findall(r"<td[^>]*>(.*?)</td>", tb.group(1), re.DOTALL | re.IGNORECASE)]
    if len(cells) < 6: return None
    try:
        students = int(cells[0])
        pf = re.match(r"(\d+)/(\d+)", cells[1])
        avg = float(cells[5])
        if not (0 < students <= 10000 and 0 <= avg <= 100): return None
        return {
            "students":     students,
            "pass_n":       int(pf.group(1)) if pf else None,
            "fail_n":       int(pf.group(2)) if pf else None,
            "pass_pct":     float(cells[2]) if len(cells) > 2 else None,
            "min_grade":    float(cells[3]) if len(cells) > 3 else None,
            "max_grade":    float(cells[4]) if len(cells) > 4 else None,
            "avg_grade":    avg,
            "median_grade": float(cells[6]) if len(cells) > 6 else None,
        }
    except (ValueError, IndexError): return None

def _extract_finals_for_sem(html, sem):
    agg = re.search(rf'id="{re.escape(sem)}-Finals?"', html, re.IGNORECASE)
    if agg:
        r = _parse_finals_table(html[agg.start(): agg.start() + 2000])
        if r: return r
    parts = []
    for m in re.finditer(rf'id="({re.escape(sem)}-Final_[^"]+)"', html, re.IGNORECASE):
        r = _parse_finals_table(html[m.start(): m.start() + 2000])
        if r: parts.append(r)
    if not parts: return None
    total_n = sum(p["students"] for p in parts)
    pass_total = sum(p["pass_n"] for p in parts if p["pass_n"] is not None)
    return {
        "students":     total_n,
        "pass_n":       pass_total,
        "fail_n":       sum(p["fail_n"] for p in parts if p["fail_n"] is not None),
        "pass_pct":     round(100 * pass_total / total_n, 1) if total_n else None,
        "min_grade":    min(p["min_grade"] for p in parts if p["min_grade"] is not None),
        "max_grade":    max(p["max_grade"] for p in parts if p["max_grade"] is not None),
        "avg_grade":    sum(p["avg_grade"] * p["students"] for p in parts) / total_n,
        "median_grade": None,
    }

async def fetch_new_grades(page, course_id, known_semesters):
    """
    Fetch histogram page for course_id.
    Returns dict of sem -> grade_data for semesters NOT already in known_semesters.
    """
    try:
        await page.goto(f"{HIST_BASE}/{course_id}/", wait_until="domcontentloaded", timeout=60000)
        try: await page.wait_for_selector("text=סופי", timeout=8000)
        except PlaywrightTimeout: pass
        await page.wait_for_timeout(1000)
        html = await page.content()
    except Exception as e:
        print(f"  [hist] Error {course_id}: {e}")
        return {}

    all_sems = sorted(set(re.findall(r'id="(\d{6})-[Ff]inal', html)))
    new = {}
    for sem in all_sems:
        if sem in known_semesters:
            continue
        r = _extract_finals_for_sem(html, sem)
        if r:
            new[sem] = r
    return new

# ── Aggregation helper ────────────────────────────────────────────────────────
def recompute_agg(course_id, per_sem_rows):
    """Recompute aggregated stats for a course from all its per-sem rows."""
    hparts = [r for r in per_sem_rows.values()
              if r.get("course_id") == course_id and r.get("avg_grade")]
    if not hparts:
        return None
    total_n = sum(float(h["students"]) for h in hparts)
    agg_grade = sum(float(h["avg_grade"]) * float(h["students"]) for h in hparts) / total_n
    agg_pass  = (sum(float(h["pass_pct"]) * float(h["students"]) for h in hparts
                     if h.get("pass_pct")) / total_n) if any(h.get("pass_pct") for h in hparts) else None
    sems = sorted(h["semester"] for h in hparts)
    return {
        "avg_final_grade": agg_grade,
        "avg_pass_pct":    agg_pass,
        "total_students":  int(total_n),
        "n_semesters":     len(hparts),
        "semester_range":  f"{sems[0]} to {sems[-1]}",
    }

# ── Main ──────────────────────────────────────────────────────────────────────
async def main():
    # Determine which courses to check
    check_all = "--all" in sys.argv
    explicit_sem = next((a for a in sys.argv[1:] if re.match(r"^\d{6}$", a)), None)

    if explicit_sem:
        json_path = os.path.join(OUTPUT_DIR, f"semester_{explicit_sem}.json")
    else:
        # Find latest semester JSON
        import glob
        jsons = sorted(glob.glob(os.path.join(OUTPUT_DIR, "semester_*.json")))
        if not jsons:
            print("No semester_*.json found. Run update_latest.py first.")
            sys.exit(1)
        json_path = jsons[-1]

    with open(json_path, encoding="utf-8") as f:
        sem_data = json.load(f)

    semester  = sem_data["semester"]
    to_check  = sem_data["courses"]
    print(f"Checking {len(to_check)} courses from semester {semester}")

    # Load existing data
    per_sem = load_per_sem()
    agg     = load_agg()

    # Figure out which courses actually need checking
    # (those that don't already have grades for this semester)
    need_check = [cid for cid in to_check if (cid, semester) not in per_sem]
    already    = len(to_check) - len(need_check)
    print(f"  {already} already have grades for {semester}, checking {len(need_check)} new ones")

    if not need_check:
        print("Nothing to update.")
        return

    new_per_sem_rows = []
    updated_agg      = set()

    async with async_playwright() as p:
        browser  = await p.chromium.launch(headless=True)
        page     = await (await browser.new_context()).new_page()

        for i, cid in enumerate(need_check, 1):
            # Get semesters we already have for this course
            known = {k[1] for k in per_sem if k[0] == cid}

            print(f"[{i}/{len(need_check)}] {cid}  (known sems: {len(known)})", end="", flush=True)

            new_grades = await fetch_new_grades(page, cid, known)

            if not new_grades:
                print(" — no new grades")
                continue

            print(f" — {len(new_grades)} new semester(s): {sorted(new_grades.keys())}")

            # Get course name from existing agg row or per_sem
            name = ""
            if cid in agg:
                name = agg[cid].get("course_name", "")
            else:
                existing = next((r for k, r in per_sem.items() if k[0] == cid), None)
                if existing:
                    name = existing.get("course_name", "")

            for sem, h in new_grades.items():
                row = {
                    "course_id":        cid,
                    "course_name":      name,
                    "semester":         sem,
                    "semester_label":   sem_to_label(sem),
                    "students":         h["students"],
                    "pass_n":           h["pass_n"] if h["pass_n"] is not None else "",
                    "fail_n":           h["fail_n"] if h["fail_n"] is not None else "",
                    "pass_pct":         fmt(h["pass_pct"]) if h["pass_pct"] is not None else "",
                    "min_grade":        fmt(h["min_grade"]) if h["min_grade"] is not None else "",
                    "max_grade":        fmt(h["max_grade"]) if h["max_grade"] is not None else "",
                    "avg_grade":        fmt(h["avg_grade"]) if h["avg_grade"] is not None else "",
                    "median_grade":     fmt(h["median_grade"]) if h["median_grade"] is not None else "",
                    "avg_general_rank": agg.get(cid, {}).get("avg_general_rank", ""),
                    "n_general_rank":   agg.get(cid, {}).get("n_general_rank", ""),
                    "hist_url":         f"{HIST_BASE}/{cid}/",
                    "cf_url":           f"https://cheesefork.cf/?course={cid}&semester={sem}",
                }
                per_sem[(cid, sem)] = row
                new_per_sem_rows.append(row)
                updated_agg.add(cid)

        await browser.close()

    if not new_per_sem_rows:
        print("\nNo new grades found for any course.")
        return

    print(f"\nFound new grades for {len(updated_agg)} courses ({len(new_per_sem_rows)} new semester rows)")

    # ── Write updated per-semester CSV ────────────────────────────────────────
    PER_SEM_COLS = ["course_id","course_name","semester","semester_label",
                    "students","pass_n","fail_n","pass_pct",
                    "min_grade","max_grade","avg_grade","median_grade",
                    "avg_general_rank","n_general_rank","hist_url","cf_url"]

    all_rows = sorted(per_sem.values(), key=lambda r: (r["course_id"], r["semester"]))
    with open(PER_SEM_CSV, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=PER_SEM_COLS)
        w.writeheader()
        w.writerows(all_rows)
    print(f"Updated {PER_SEM_CSV}  ({len(all_rows)} total rows)")

    # ── Update aggregated CSV for affected courses ────────────────────────────
    AGG_COLS = ["course_id","course_name","semester_range","n_semesters",
                "avg_final_grade","total_students","avg_pass_pct",
                "avg_general_rank","n_general_rank","hist_url","cf_url"]

    for cid in updated_agg:
        stats = recompute_agg(cid, per_sem)
        if not stats:
            continue
        if cid in agg:
            agg[cid].update({
                "avg_final_grade": fmt(stats["avg_final_grade"]),
                "avg_pass_pct":    fmt(stats["avg_pass_pct"]) if stats["avg_pass_pct"] else "",
                "total_students":  stats["total_students"],
                "n_semesters":     stats["n_semesters"],
                "semester_range":  stats["semester_range"],
            })
        else:
            # Brand new course — add it
            name = new_per_sem_rows[0]["course_name"] if new_per_sem_rows else ""
            agg[cid] = {
                "course_id":        cid,
                "course_name":      name,
                "semester_range":   stats["semester_range"],
                "n_semesters":      stats["n_semesters"],
                "avg_final_grade":  fmt(stats["avg_final_grade"]),
                "total_students":   stats["total_students"],
                "avg_pass_pct":     fmt(stats["avg_pass_pct"]) if stats["avg_pass_pct"] else "",
                "avg_general_rank": "",
                "n_general_rank":   "",
                "hist_url":         f"{HIST_BASE}/{cid}/",
                "cf_url":           f"https://cheesefork.cf/?course={cid}&semester={semester}",
            }

    all_agg = sorted(agg.values(), key=lambda r: r["course_id"])
    with open(AGG_CSV, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=AGG_COLS)
        w.writeheader()
        w.writerows(all_agg)
    print(f"Updated {AGG_CSV}  ({len(all_agg)} total rows)")
    print(f"\nDone. {len(updated_agg)} courses updated.")

asyncio.run(main())