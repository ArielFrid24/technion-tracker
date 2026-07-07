"""
add_free_choice.py
Finds all courses starting with 03 from courses_aggregated_all.csv
that are NOT already labeled as מלג or קורס ספורט,
scrapes credits & prereqs from CheeseFork,
appends to courses_labeled.csv with category=בחירה חופשית.
"""
import asyncio, csv, os, re
from playwright.async_api import async_playwright

from scraper_common import discover_semesters, scrape_cf_course

OUTPUT_DIR  = "C:/Users/pc/OneDrive - Technion/Desktop/cheesefork_scraper"
LABELED_CSV = os.path.join(OUTPUT_DIR, "courses_labeled.csv")
AGG_CSV     = os.path.join(OUTPUT_DIR, "courses_aggregated_all.csv")
BASE        = "https://cheesefork.cf/"

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

    # Skip מלג and ספורט courses — they have their own categories
    already_labeled = {
        r["course_id"] for r in existing_rows
        if r.get("category") in ("מלג", "קורס ספורט")
    }

    # Scrape all 03xxxxxx courses (not sport, not already labeled),
    # then keep only those with <=2 credits and no prereqs
    candidate_courses = sorted(
        cid for cid in agg
        if cid.startswith("03")
        and cid not in already_labeled
        and not cid.startswith("0394")
    )
    print(f"Found {len(candidate_courses)} candidate 03xxxxxx courses to check")

    # Remove old בחירה חופשית rows
    existing_rows = [r for r in existing_rows if r.get("category") != "בחירה חופשית"]

    print(f"Scraping {len(candidate_courses)} candidate 03x courses...")
    new_rows = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page    = await (await browser.new_context()).new_page()
        semester_fallback = list(reversed(await discover_semesters(page)))

        for i, cid in enumerate(candidate_courses, 1):
            ex = agg[cid]
            name_csv = ex.get("course_name", "")
            print(f"[{i}/{len(candidate_courses)}] {cid}", end="  ")

            name_scraped, credits, prereqs = await scrape_cf_course(page, cid, semester_fallback)
            name = name_csv or name_scraped

            # Keep only courses with <=2 credits and no prereqs
            if credits is None or credits > 2 or prereqs:
                print(f"credits={credits}  prereqs={prereqs or '-'}  SKIP")
                continue

            print(f"credits={credits}  ✓  {name[:35] if name else '?'}")
            new_rows.append({
                "course_id":        cid,
                "course_name":      name,
                "category":         "בחירה חופשית",
                "credits":          credits,
                "prereqs":          "",
                "avg_final_grade":  ex.get("avg_final_grade", ""),
                "avg_general_rank": ex.get("avg_general_rank", ""),
                "n_general_rank":   ex.get("n_general_rank", ""),
                "semester":         "",
            })

        await browser.close()
    print(f"Kept {len(new_rows)} courses with <=2 credits and no prereqs")

    all_rows = existing_rows + new_rows

    with open(LABELED_CSV, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(all_rows)

    print(f"\nSaved {len(all_rows)} total rows -> {LABELED_CSV}")
    print(f"  בחירה חופשית: {len(new_rows)}")

asyncio.run(main())