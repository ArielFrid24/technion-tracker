"""
add_science.py
Scrapes credits, prereqs from CheeseFork for קורסי מדעי,
joins grades from courses_aggregated_all.csv,
appends to courses_labeled.csv with category=קורס מדעי.
"""
import asyncio, csv, os, re
from playwright.async_api import async_playwright

from scraper_common import discover_semesters, scrape_cf_course

OUTPUT_DIR  = "C:/Users/pc/OneDrive - Technion/Desktop/cheesefork_scraper"
LABELED_CSV = os.path.join(OUTPUT_DIR, "courses_labeled.csv")
AGG_CSV     = os.path.join(OUTPUT_DIR, "courses_aggregated_all.csv")
BASE        = "https://cheesefork.cf/"

SCIENCE_COURSES = [
    "01140032",
    "01140052",
    "01140054",
    "01140075",
    "01240120",
    "01240510",
    "01250001",
    "01250013",
    "01250801",
    "01340020",
    "01340058",
    "02740300",
]

def load_agg():
    data = {}
    if not os.path.exists(AGG_CSV):
        return data
    with open(AGG_CSV, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            data[row["course_id"]] = row
    return data

def load_labeled():
    if not os.path.exists(LABELED_CSV):
        return [], []
    with open(LABELED_CSV, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        cols = list(reader.fieldnames or [])
        rows = list(reader)
    return rows, cols

async def main():
    existing_rows, cols = load_labeled()
    agg = load_agg()

    if "semester" not in cols:
        cols = cols + ["semester"]

    # Remove old קורס מדעי rows
    existing_rows = [r for r in existing_rows if r.get("category") != "קורס מדעי"]

    print(f"Scraping {len(SCIENCE_COURSES)} קורסי מדעי from CheeseFork...\n")

    new_rows = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page    = await (await browser.new_context()).new_page()
        semester_fallback = list(reversed(await discover_semesters(page)))

        for i, cid in enumerate(SCIENCE_COURSES, 1):
            ex = agg.get(cid, {})
            name_csv = ex.get("course_name", "")
            print(f"[{i}/{len(SCIENCE_COURSES)}] {cid}", end="  ")

            name_scraped, credits, prereqs = await scrape_cf_course(page, cid, semester_fallback)
            name = name_csv or name_scraped
            print(f"credits={credits}  prereqs={prereqs or '-'}  {name[:35] if name else '?'}")

            new_rows.append({
                "course_id":        cid,
                "course_name":      name,
                "category":         "קורס מדעי",
                "credits":          credits if credits is not None else "",
                "prereqs":          prereqs,
                "avg_final_grade":  ex.get("avg_final_grade", ""),
                "avg_general_rank": ex.get("avg_general_rank", ""),
                "n_general_rank":   ex.get("n_general_rank", ""),
                "semester":         "",
            })

        await browser.close()

    all_rows = existing_rows + new_rows

    with open(LABELED_CSV, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(all_rows)

    print(f"\nSaved {len(all_rows)} total rows -> {LABELED_CSV}")
    print(f"  קורס מדעי: {len(new_rows)}")

asyncio.run(main())