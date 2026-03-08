"""
recommender.py
Technion degree course recommender.

Usage:
    python recommender.py --semester 202502 --min 9 --max 12
    python recommender.py --semester 202502 --min 9 --max 12 --must 00960411 00940224
    python recommender.py --semester 202502 --min 9 --max 12 --taken taken.json
    python recommender.py --add-taken 00960200 00960208   # add to taken.json
    python recommender.py --status                        # show degree progress
"""

import argparse, asyncio, csv, json, os, re, sys
from itertools import combinations
from playwright.async_api import async_playwright

OUTPUT_DIR   = "C:/Users/pc/OneDrive - Technion/Desktop/cheesefork_scraper"
LABELED_CSV  = os.path.join(OUTPUT_DIR, "courses_labeled.csv")
TAKEN_JSON   = os.path.join(OUTPUT_DIR, "taken.json")
BASE         = "https://cheesefork.cf/"

# ── Degree requirements ────────────────────────────────────────────────────────
REQ = {
    "חובה":               108.0,   # mandatory (includes 5.5 science)
    "קורס מדעי":           5.5,    # science (subset of mandatory)
    "בחירה בנתונים":      24.5,    # data electives
    "עתיר נתונים":         2.0,    # min עתיר within data electives
    "בחירה פקולטית":      10.5,    # faculty electives
    "קורס ספורט":          2,      # courses (not points)
    "מלג":                 2,      # courses (not points)
    "בחירה חופשית":        6.0,    # free choice points
    "total":             155.0,
}

# Categories that count toward בחירה חופשית after mandatory quota is met
FREE_ELIGIBLE = {"מלג", "קורס ספורט", "בחירה חופשית"}

# ── Load courses DB ────────────────────────────────────────────────────────────
def load_courses():
    courses = {}
    if not os.path.exists(LABELED_CSV):
        print(f"ERROR: {LABELED_CSV} not found"); sys.exit(1)
    with open(LABELED_CSV, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            try: credits = float(row.get("credits") or 0)
            except: credits = 0
            try: grade = float(row.get("avg_final_grade") or 0)
            except: grade = 0
            courses[row["course_id"]] = {
                "id":       row["course_id"],
                "name":     row["course_name"],
                "category": row["category"],
                "credits":  credits,
                "grade":    grade,
                "prereqs":  row.get("prereqs", "").strip(),
                "semester": row.get("semester", ""),
            }
    return courses

# ── Load / save taken courses ──────────────────────────────────────────────────
def load_taken(path=None):
    p = path or TAKEN_JSON
    if os.path.exists(p):
        with open(p) as f:
            return set(json.load(f))
    return set()

def save_taken(taken, path=None):
    p = path or TAKEN_JSON
    with open(p, "w") as f:
        json.dump(sorted(taken), f, indent=2)
    print(f"Saved {len(taken)} taken courses -> {p}")

# ── Prerequisite checker ───────────────────────────────────────────────────────
def prereqs_met(course, taken_ids, courses_db):
    """Check if all prerequisites are satisfied given taken course IDs."""
    prereq_str = course.get("prereqs", "").strip()
    if not prereq_str:
        return True

    # Parse: "A AND B OR C AND D" → evaluate left to right
    # Groups separated by OR; within each group, all AND conditions must be met
    tokens = prereq_str.split()
    # Split into OR groups
    or_groups = []
    current_group = []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t == "OR":
            or_groups.append(current_group)
            current_group = []
        elif t == "AND":
            pass  # next token is another required course in same group
        else:
            current_group.append(t)
        i += 1
    if current_group:
        or_groups.append(current_group)

    # At least one OR group must be fully satisfied
    for group in or_groups:
        if all(cid in taken_ids for cid in group):
            return True
    return False

# ── Degree progress ────────────────────────────────────────────────────────────
def compute_progress(taken_ids, courses_db):
    """Return dict of progress per requirement bucket."""
    prog = {
        "חובה_pts":          0.0,
        "קורס מדעי_pts":     0.0,
        "בחירה בנתונים_pts": 0.0,
        "עתיר נתונים_pts":   0.0,
        "בחירה פקולטית_pts": 0.0,
        "קורס ספורט_n":      0,
        "מלג_n":             0,
        "בחירה חופשית_pts":  0.0,
        "total_pts":         0.0,
    }
    sport_done  = 0
    malag_done  = 0

    for cid in taken_ids:
        c = courses_db.get(cid)
        if not c: continue
        cat = c["category"]
        cr  = c["credits"]

        if cat == "חובה":
            prog["חובה_pts"] += cr
        elif cat == "קורס מדעי":
            prog["קורס מדעי_pts"] += cr
            prog["חובה_pts"] += cr  # science counts toward mandatory
        elif cat in ("בחירה בנתונים", "עתיר נתונים"):
            prog["בחירה בנתונים_pts"] += cr
            if cat == "עתיר נתונים":
                prog["עתיר נתונים_pts"] += cr
        elif cat == "קורסי בחירה פקולטיים":
            prog["בחירה פקולטית_pts"] += cr
        elif cat == "קורס ספורט":
            sport_done += 1
            prog["קורס ספורט_n"] = sport_done
            if sport_done <= 2:
                pass  # mandatory, counted in total later
            else:
                prog["בחירה חופשית_pts"] += cr
        elif cat == "מלג":
            malag_done += 1
            prog["מלג_n"] = malag_done
            if malag_done <= 2:
                pass
            else:
                prog["בחירה חופשית_pts"] += cr
        elif cat == "בחירה חופשית":
            prog["בחירה חופשית_pts"] += cr

        prog["total_pts"] += cr

    return prog

def print_status(taken_ids, courses_db):
    prog = compute_progress(taken_ids, courses_db)
    print("\n══ Degree Progress ══════════════════════════════")
    def bar(done, req, unit="pts"):
        pct = min(done/req*100, 100) if req else 100
        filled = int(pct/5)
        b = "█"*filled + "░"*(20-filled)
        status = "✓" if done >= req else f"{req-done:.1f} {unit} missing"
        return f"[{b}] {done:.1f}/{req:.1f} {unit}  {status}"

    print(f"  חובה (mandatory):        {bar(prog['חובה_pts'], REQ['חובה'])}")
    print(f"    ↳ קורס מדעי (science): {bar(prog['קורס מדעי_pts'], REQ['קורס מדעי'])}")
    print(f"  בחירה בנתונים:           {bar(prog['בחירה בנתונים_pts'], REQ['בחירה בנתונים'])}")
    print(f"    ↳ עתיר נתונים (min):   {bar(prog['עתיר נתונים_pts'], REQ['עתיר נתונים'])}")
    print(f"  בחירה פקולטית:           {bar(prog['בחירה פקולטית_pts'], REQ['בחירה פקולטית'])}")
    sport_n = prog['קורס ספורט_n']
    malag_n = prog['מלג_n']
    sport_status = '✓' if sport_n >= 2 else f'{2-sport_n} missing'
    malag_status = '✓' if malag_n >= 2 else f'{2-malag_n} missing'
    print(f"  קורס ספורט:              [{sport_n}/2 courses]  {sport_status}")
    print(f"  מלג:                     [{malag_n}/2 courses]  {malag_status}")
    print(f"  בחירה חופשית:            {bar(prog['בחירה חופשית_pts'], REQ['בחירה חופשית'])}")
    print(f"  ─────────────────────────────────────────────")
    print(f"  TOTAL:                   {bar(prog['total_pts'], REQ['total'])}")
    print()

# ── Scrape available courses for a semester ────────────────────────────────────
async def get_available_courses(semester, courses_db):
    """Return set of course IDs offered in the given semester."""
    print(f"Fetching courses available in semester {semester}...")
    available = set()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page    = await (await browser.new_context()).new_page()
        try:
            await page.goto(f"{BASE}?semester={semester}",
                            wait_until="domcontentloaded", timeout=30000)
            try: await page.wait_for_load_state("networkidle", timeout=10000)
            except: pass
            html = await page.content()
            hrefs = await page.eval_on_selector_all(
                "a[href]", "els => els.map(e=>e.getAttribute('href'))")
            all_text = html + "\n".join(hrefs or [])
            found = set(re.findall(r"[?&]course=(\d{7,8})\b", all_text))
            available = {c.zfill(8) for c in found}
        except Exception as e:
            print(f"  Warning: could not fetch semester page: {e}")
        await browser.close()

    # Intersect with known courses
    known_available = available & set(courses_db.keys())
    print(f"  Found {len(available)} courses on CheeseFork, "
          f"{len(known_available)} in our labeled DB")
    return known_available

# ── Optimizer ─────────────────────────────────────────────────────────────────
def weighted_grade(course_list):
    total_cr = sum(c["credits"] for c in course_list)
    if total_cr == 0: return 0
    return sum(c["credits"] * c["grade"] for c in course_list) / total_cr

def recommend(available_ids, taken_ids, courses_db, min_pts, max_pts, must_take=None):
    must_take = set(must_take or [])
    prog = compute_progress(taken_ids, courses_db)

    # Filter to courses that are:
    # 1. Available this semester
    # 2. Not already taken
    # 3. Prereqs satisfied (considering must_take as also "taken" for prereq purposes)
    assumed_taken = taken_ids | must_take
    candidates = []
    for cid in available_ids:
        if cid in taken_ids: continue
        c = courses_db.get(cid)
        if not c or c["credits"] == 0: continue
        if not prereqs_met(c, assumed_taken, courses_db): continue
        candidates.append(c)

    # Separate mandatory must-takes
    must_courses = [courses_db[cid] for cid in must_take
                    if cid in courses_db]
    must_pts = sum(c["credits"] for c in must_courses)

    print(f"\n  {len(candidates)} eligible courses after prereq filter")
    print(f"  Must-take: {len(must_courses)} courses ({must_pts} pts)")
    print(f"  Target: [{min_pts}, {max_pts}] pts\n")

    # Remove must-takes from candidates pool
    free_candidates = [c for c in candidates if c["id"] not in must_take]

    # Sort by grade descending for greedy seed
    free_candidates.sort(key=lambda c: c["grade"], reverse=True)

    # Try all combinations up to a reasonable size
    # For large candidate pools, limit to top-N by grade
    MAX_CANDIDATES = 25
    if len(free_candidates) > MAX_CANDIDATES:
        print(f"  (Limiting search to top {MAX_CANDIDATES} courses by grade)")
        free_candidates = free_candidates[:MAX_CANDIDATES]

    best_schedule = None
    best_score    = -1

    remaining_min = max(0, min_pts - must_pts)
    remaining_max = max(0, max_pts - must_pts)

    # Try combinations of 0..N additional courses
    for n in range(len(free_candidates) + 1):
        for combo in combinations(free_candidates, n):
            combo_pts = sum(c["credits"] for c in combo)
            total_pts = must_pts + combo_pts
            if total_pts < min_pts or total_pts > max_pts:
                continue
            schedule = must_courses + list(combo)
            score = weighted_grade(schedule)
            if score > best_score:
                best_score    = score
                best_schedule = schedule

    return best_schedule, best_score

# ── CLI ────────────────────────────────────────────────────────────────────────
async def main():
    parser = argparse.ArgumentParser(description="Technion course recommender")
    parser.add_argument("--semester",    help="Semester code e.g. 202502")
    parser.add_argument("--min",         type=float, help="Min credits to take")
    parser.add_argument("--max",         type=float, help="Max credits to take")
    parser.add_argument("--must",        nargs="*",  help="Course IDs you must take")
    parser.add_argument("--taken",       help="Path to taken.json (default: taken.json)")
    parser.add_argument("--add-taken",   nargs="+",  help="Add course IDs to taken list")
    parser.add_argument("--remove-taken",nargs="+",  help="Remove course IDs from taken list")
    parser.add_argument("--show-taken",  action="store_true", help="List taken courses")
    parser.add_argument("--status",      action="store_true", help="Show degree progress")
    args = parser.parse_args()

    courses_db = load_courses()
    taken_json = args.taken or TAKEN_JSON
    taken_ids  = load_taken(taken_json)

    # ── Manage taken list ──────────────────────────────────────────────────────
    if args.add_taken:
        for cid in args.add_taken:
            cid = cid.zfill(8)
            name = courses_db.get(cid, {}).get("name", "unknown")
            taken_ids.add(cid)
            print(f"  + {cid} {name}")
        save_taken(taken_ids, taken_json)
        return

    if args.remove_taken:
        for cid in args.remove_taken:
            cid = cid.zfill(8)
            taken_ids.discard(cid)
            print(f"  - {cid}")
        save_taken(taken_ids, taken_json)
        return

    if args.show_taken:
        print(f"\nTaken courses ({len(taken_ids)}):")
        for cid in sorted(taken_ids):
            c = courses_db.get(cid, {})
            print(f"  {cid}  {c.get('credits','?')}pt  {c.get('name','?')}")
        return

    if args.status:
        print_status(taken_ids, courses_db)
        return

    # ── Recommend ──────────────────────────────────────────────────────────────
    if not args.semester:
        parser.error("--semester required for recommendations")
    if args.min is None or args.max is None:
        parser.error("--min and --max required for recommendations")

    available_ids = await get_available_courses(args.semester, courses_db)
    must_take = [cid.zfill(8) for cid in (args.must or [])]

    print(f"\nSearching for best schedule [{args.min}–{args.max} pts]...")
    schedule, score = recommend(
        available_ids, taken_ids, courses_db,
        args.min, args.max, must_take
    )

    if not schedule:
        print("No valid schedule found within the given point range.")
        return

    print(f"\n{'═'*55}")
    print(f"  Recommended schedule  (weighted avg grade: {score:.1f})")
    print(f"{'═'*55}")
    total_pts = sum(c["credits"] for c in schedule)
    for c in sorted(schedule, key=lambda x: x["grade"], reverse=True):
        tag = " ← MUST" if c["id"] in set(must_take) else ""
        print(f"  {c['id']}  {c['credits']:4.1f}pt  grade:{c['grade']:5.1f}  "
              f"[{c['category']}]  {c['name'][:35]}{tag}")
    print(f"{'─'*55}")
    print(f"  Total: {total_pts:.1f} pts   Weighted avg grade: {score:.1f}")

    # Show updated progress after taking this schedule
    new_taken = taken_ids | {c["id"] for c in schedule}
    print()
    print_status(new_taken, courses_db)

asyncio.run(main())