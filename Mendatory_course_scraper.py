"""
add_mandatory.py
Scrapes credits and prereqs for mandatory courses (קורסי חובה),
then appends them to courses_labeled.csv with category=חובה and semester number.
Courses already in the CSV are skipped for scraping but still added/updated.
"""
import asyncio, csv, os, re
from playwright.async_api import async_playwright

from scraper_common import discover_semesters, scrape_cf_course

from scraper_common import discover_semesters, scrape_cf_course

OUTPUT_DIR  = "C:/Users/pc/OneDrive - Technion/Desktop/cheesefork_scraper"
LABELED_CSV = os.path.join(OUTPUT_DIR, "courses_labeled.csv")
OUT_CSV     = os.path.join(OUTPUT_DIR, "courses_labeled.csv")  # overwrite in place
AGG_CSV     = os.path.join(OUTPUT_DIR, "courses_aggregated_all.csv")
BASE        = "https://cheesefork.cf/"

# ── Mandatory courses per semester ────────────────────────────────────────────
MANDATORY = [
    # (course_id, semester)
    ("00940345", 1),
    ("01040031", 1),
    ("01040166", 1),
    ("02340117", 1),
    ("03240033", 1),

    ("00940700", 2),
    ("00940219", 2),
    ("00940210", 2),
    ("00960412", 2),
    ("01040032", 2),
    ("01140051", 2),

    ("00940224", 3),
    ("00940241", 3),
    ("00940424", 3),
    ("00950296", 3),
    ("00960570", 3),

    ("00940314", 4),
    ("00960211", 4),
    ("00960224", 4),
    ("00960327", 4),
    ("00960411", 4),
    ("00970414", 4),

    ("00960210", 5),
    ("00960250", 5),
    ("00960275", 5),
    ("00970209", 5),
    ("00970447", 5),

    ("00940290", 7),
    ("00940295", 8),
]

# ── Load existing aggregated grades ───────────────────────────────────────────
def load_agg():
    data = {}
    if not os.path.exists(AGG_CSV):
        return data
    with open(AGG_CSV, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            data[row["course_id"]] = row
    return data

# ── Load already-labeled CSV ───────────────────────────────────────────────────
def load_labeled():
    rows = []
    if not os.path.exists(LABELED_CSV):
        return [], []
    with open(LABELED_CSV, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        cols = reader.fieldnames or []
        rows = list(reader)
    return rows, cols

# ── Main ───────────────────────────────────────────────────────────────────────
async def main():
    existing_rows, cols = load_labeled()
    agg = load_agg()

    # Ensure semester column exists
    if "semester" not in cols:
        cols = cols + ["semester"] if cols else \
            ["course_id","course_name","category","credits","prereqs",
             "avg_final_grade","avg_general_rank","n_general_rank","semester"]

    # Index existing rows by course_id
    existing_ids = {r["course_id"] for r in existing_rows}
    mandatory_ids = {cid for cid, _ in MANDATORY}

    # Remove any existing חובה rows (we'll re-add them fresh)
    existing_rows = [r for r in existing_rows if r.get("category") != "חובה"]

    # Ensure all existing rows have a semester field
    for r in existing_rows:
        if "semester" not in r:
            r["semester"] = ""

    print(f"Loaded {len(existing_rows)} existing non-חובה rows")
    print(f"Scraping {len(MANDATORY)} mandatory courses...\n")

    mandatory_results = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page    = await (await browser.new_context()).new_page()
        semester_fallback = list(reversed(await discover_semesters(page)))

        for i, (cid, sem) in enumerate(MANDATORY, 1):
            ex = agg.get(cid, {})
            name_csv = ex.get("course_name", "")

            print(f"[{i}/{len(MANDATORY)}] {cid} sem={sem}", end="  ")
            name_scraped, credits, prereqs = await scrape_cf_course(page, cid, semester_fallback)
            name = name_csv or name_scraped

            print(f"credits={credits}  prereqs={prereqs or '-'}  {name[:35] if name else '?'}")

            mandatory_results.append({
                "course_id":        cid,
                "course_name":      name,
                "category":         "חובה",
                "credits":          credits if credits is not None else "",
                "prereqs":          prereqs,
                "avg_final_grade":  ex.get("avg_final_grade", ""),
                "avg_general_rank": ex.get("avg_general_rank", ""),
                "n_general_rank":   ex.get("n_general_rank", ""),
                "semester":         sem,
            })

        await browser.close()

    # Combine: existing rows first, then mandatory
    all_rows = existing_rows + mandatory_results

    with open(OUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(all_rows)

    print(f"\nSaved {len(all_rows)} total rows -> {OUT_CSV}")
    print(f"  חובה:                  {len(mandatory_results)}")
    print(f"  other:                 {len(existing_rows)}")

asyncio.run(main())