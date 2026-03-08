"""
scrape_courses.py
- Scrapes credit points for courses from CheeseFork
- Joins with existing avg_grade from courses_aggregated_all.csv
- Labels courses as:
    עתיר נתונים         — starred courses in the provided list
    קורסי בחירה בנתונים — non-starred courses in the provided list
    קורסי בחירה פקולטיים — 09xxxxx courses from CheeseFork NOT in the list
"""
import asyncio, csv, os, re
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

OUTPUT_DIR = "C:/Users/pc/OneDrive - Technion/Desktop/cheesefork_scraper"
AGG_CSV    = os.path.join(OUTPUT_DIR, "courses_aggregated_all.csv")
OUT_CSV    = os.path.join(OUTPUT_DIR, "courses_labeled.csv")
BASE       = "https://cheesefork.cf/"
DISCOVERY_SEMESTER = "202502"  # latest semester

# ── Provided course list ───────────────────────────────────────────────────────
# (course_id, is_עתיר)
COURSE_LIST = [
    ("00940703", False), ("00950280", False),
    ("00960200", False), ("00960208", False), ("00960212", False), ("00960251", False),
    ("00960222", True),  ("00960226", False), ("00960231", True),  ("00960235", True),
    ("00960236", False), ("00960244", False), ("00960262", True),  ("00960620", False),
    ("00960265", False), ("00960290", False), ("00960291", False), ("00906292", False),
    ("00960293", False), ("00960311", False), ("00960324", True),  ("00960335", False),
    ("00960336", False), ("00960401", False), ("00960412", False), ("00960414", False),
    ("00960415", False), ("00960425", False), ("00960450", False), ("00960470", False),
    ("00960475", False), ("00960573", False), ("00960576", False), ("00960578", False),
    ("00960589", False), ("00960625", False), ("00960693", True),  ("00970135", True),
    ("00970200", True),  ("00970201", False), ("00970211", False), ("00970215", True),
    ("00970216", True),  ("00970217", False), ("00970222", True),  ("00970244", False),
    ("00970245", False), ("00970246", False), ("00970247", True),  ("00970248", True),
    ("00970249", False), ("00970252", False), ("00970272", True),  ("00970280", False),
    ("00970317", False), ("00970325", False), ("00970329", False), ("00970400", True),
    ("00970449", False), ("00970702", False), ("00970920", False), ("00970980", False),
]

LIST_IDS = {cid for cid, _ in COURSE_LIST}
STAR_IDS = {cid for cid, star in COURSE_LIST if star}

def categorize(course_id):
    if course_id in STAR_IDS:
        return "עתיר נתונים"
    elif course_id in LIST_IDS:
        return "קורסי בחירה בנתונים"
    else:
        return "קורסי בחירה פקולטיים"

# ── Load existing grades ───────────────────────────────────────────────────────
def load_existing():
    data = {}
    if not os.path.exists(AGG_CSV):
        print(f"[warn] {AGG_CSV} not found")
        return data
    with open(AGG_CSV, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            data[row["course_id"]] = row
    return data

# ── Scrape credit points ───────────────────────────────────────────────────────
async def scrape_credits_and_name(page, course_id):
    for sem in ("202502", "202501", "202403", "202402", "202401", "202303", "202302", "202301"):
        try:
            await page.goto(f"{BASE}?course={course_id}&semester={sem}",
                            wait_until="domcontentloaded", timeout=30000)
            try: await page.wait_for_selector("text=נקודות", timeout=3000)
            except: pass
            try: await page.wait_for_load_state("networkidle", timeout=3000)
            except: pass

            title = await page.title()
            if course_id.lstrip("0") not in title and course_id not in title:
                continue

            name = ""
            m = re.match(r"^\s*[\d\s]*-\s*(.+?)\s*-", title)
            if m: name = m.group(1).strip()

            body_text = await page.inner_text("body")

            # Credits
            # Match "נקודות: X" or "נקודות זכות: X" — colon required, value 1-10
            cm = re.search(r"\u05e0\u05e7\u05d5\u05d3\u05d5\u05ea[^:]*:\s*(\d+(?:\.\d+)?)", body_text)
            credits = float(cm.group(1)) if cm and float(cm.group(1)) <= 20 else None

            # Prerequisites
            prereq_str = ""
            pm = re.search(
                r"\u05de\u05e7\u05e6\u05d5\u05e2\u05d5\u05ea \u05e7\u05d3\u05dd[:\s]+([\d\u05d0\u05d5\u05d5 \(\)-]+)",
                body_text
            )
            if pm:
                raw = pm.group(1).strip()
                # Normalize: replace ו- and או- (with hyphen) to spaced versions
                raw = re.sub(r"\u05d5-", "\u05d5 ", raw)   # ו- -> ו
                raw = re.sub(r"\u05d0\u05d5-", "\u05d0\u05d5 ", raw)  # או- -> או
                raw = raw.replace("(", "").replace(")", "")
                parts = []
                for t in raw.split():
                    t = t.strip().rstrip("-")
                    if re.match(r"^\d{6,8}$", t):
                        parts.append(t.zfill(8))
                    elif t == "\u05d0\u05d5":   # או
                        parts.append("OR")
                    elif t == "\u05d5":           # ו
                        parts.append("AND")
                prereq_str = " ".join(parts)

            if credits is not None or name:
                return name, credits, prereq_str
        except:
            continue
    return "", None, ""


# ── Discover פקולטיים courses from CheeseFork ─────────────────────────────────
async def discover_faculty_courses(page):
    """Get all 09xxxxx courses from CheeseFork that are not in our list."""
    print(f"Discovering 09x courses from CheeseFork semester {DISCOVERY_SEMESTER}...")
    url = f"{BASE}?course=all&semester={DISCOVERY_SEMESTER}"
    for attempt in range(3):
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            break
        except PlaywrightTimeoutError:
            await asyncio.sleep(3)
    try: await page.wait_for_load_state("networkidle", timeout=15000)
    except: pass

    html = await page.content()
    hrefs = await page.eval_on_selector_all(
        "a[href]", "els => els.map(e=>e.getAttribute('href')).filter(Boolean)")
    all_text = html + "\n".join(hrefs)
    all_codes = set(re.findall(r"[?&]course=(\d{7,8})\b", all_text))
    all_codes = {c.zfill(8) for c in all_codes if c.lstrip("0").startswith("9")}
    faculty_codes = sorted(all_codes - LIST_IDS)
    print(f"  Found {len(all_codes)} total 09x courses, "
          f"{len(faculty_codes)} not in provided list")
    return faculty_codes

# ── Main ───────────────────────────────────────────────────────────────────────
async def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    existing = load_existing()
    print(f"Loaded {len(existing)} courses from existing CSV\n")

    results = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page    = await (await browser.new_context()).new_page()

        # 1. Discover פקולטיים courses
        faculty_courses = await discover_faculty_courses(page)

        # 2. Build full work list: provided list + faculty courses
        work_list = [(cid, categorize(cid)) for cid, _ in COURSE_LIST] + \
                    [(cid, "קורסי בחירה פקולטיים") for cid in faculty_courses]

        print(f"\nTotal courses to process: {len(work_list)}")
        print(f"  עתיר נתונים:          {sum(1 for _,c in work_list if c=='עתיר נתונים')}")
        print(f"  קורסי בחירה בנתונים:  {sum(1 for _,c in work_list if c=='קורסי בחירה בנתונים')}")
        print(f"  קורסי בחירה פקולטיים: {sum(1 for _,c in work_list if c=='קורסי בחירה פקולטיים')}\n")

        for i, (cid, category) in enumerate(work_list, 1):
            ex = existing.get(cid, {})
            name_csv = ex.get("course_name", "")

            # Skip credit scraping if we already have name+credits from a previous run
            print(f"[{i}/{len(work_list)}] {cid} {category}", end="  ")

            name_scraped, credits, prereqs = await scrape_credits_and_name(page, cid)
            name = name_csv or name_scraped

            print(f"credits={credits}  prereqs={prereqs or '-'}  {name[:30] if name else '?'}")

            results.append({
                "course_id":        cid,
                "course_name":      name,
                "category":         category,
                "credits":          credits if credits is not None else "",
                "prereqs":          prereqs,
                "avg_final_grade":  ex.get("avg_final_grade", ""),
                "avg_general_rank": ex.get("avg_general_rank", ""),
                "n_general_rank":   ex.get("n_general_rank", ""),
            })

        await browser.close()

    cols = ["course_id", "course_name", "category", "credits", "prereqs",
            "avg_final_grade", "avg_general_rank", "n_general_rank"]
    with open(OUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(results)

    print(f"\nSaved {len(results)} rows -> {OUT_CSV}")
    missing_credits = sum(1 for r in results if r["credits"] == "")
    missing_grades  = sum(1 for r in results if r["avg_final_grade"] == "")
    print(f"Missing credits: {missing_credits}/{len(results)}")
    print(f"Missing grades:  {missing_grades}/{len(results)}")

asyncio.run(main())