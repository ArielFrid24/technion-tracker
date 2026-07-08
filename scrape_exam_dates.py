"""
scrape_exam_dates.py
Checks whether courses in the "choice" categories (מלג, קורסי בחירה בנתונים,
עתיר נתונים, קורסי בחירה פקולטיים, בחירה חופשית) that are offered in the
target semester have a written exam, and writes has_test / exam_date_a /
exam_date_b columns into courses_labeled.csv:
    has_test    "1" = has exam, "0" = no exam, "" = not checked
    exam_date_a "DD-MM-YYYY" (מועד א), blank if no exam or not checked
    exam_date_b "DD-MM-YYYY" (מועד ב, if one exists), blank otherwise

CheeseFork shows "מועד א': DD-MM-YYYY" / "מועד ב': ..." on a course's page
when it has a written exam, and omits it entirely for project/lab-graded
courses — that presence/absence is the signal used here.

Usage:
    python scrape_exam_dates.py                  # uses the newest semester_*.json on disk
    python scrape_exam_dates.py --semester 202601
"""
import asyncio, csv, glob, json, os, shutil, sys
from datetime import datetime
from playwright.async_api import async_playwright

from scraper_common import check_exam_dates, run_pooled

OUTPUT_DIR  = os.path.dirname(os.path.abspath(__file__))
LABELED_CSV = os.path.join(OUTPUT_DIR, "courses_labeled.csv")
UI_PUBLIC   = os.path.join(OUTPUT_DIR, "ui", "public", "courses_labeled.csv")

TARGET_CATEGORIES = {
    "מלג", "קורסי בחירה בנתונים", "עתיר נתונים",
    "קורסי בחירה פקולטיים", "בחירה חופשית",
}
NEW_COLS = ["has_test", "exam_date_a", "exam_date_b"]

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

def latest_semester():
    jsons = sorted(glob.glob(os.path.join(OUTPUT_DIR, "semester_*.json")))
    if not jsons:
        print("No semester_*.json files found"); sys.exit(1)
    return os.path.basename(jsons[-1]).replace("semester_", "").replace(".json", "")

async def main():
    semester = None
    for i, arg in enumerate(sys.argv):
        if arg == "--semester" and i + 1 < len(sys.argv):
            semester = sys.argv[i + 1]
    if not semester:
        semester = latest_semester()
    log(f"Target semester: {semester}")

    sem_path = os.path.join(OUTPUT_DIR, f"semester_{semester}.json")
    if not os.path.exists(sem_path):
        print(f"semester_{semester}.json not found"); sys.exit(1)
    with open(sem_path, encoding="utf-8") as f:
        offered = set(json.load(f)["courses"])

    with open(LABELED_CSV, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        cols = list(reader.fieldnames)
        rows = list(reader)

    for col in NEW_COLS:
        if col not in cols:
            cols.append(col)
            for r in rows:
                r[col] = ""

    targets = [r for r in rows if r["category"] in TARGET_CATEGORIES and r["course_id"] in offered]
    log(f"{len(targets)} courses to check")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        async def worker(page, row):
            return await check_exam_dates(page, row["course_id"], semester)

        def progress(done, total):
            if done % 10 == 0 or done == total:
                log(f"  {done}/{total} checked...")

        results = await run_pooled(browser, targets, worker, concurrency=8, on_progress=progress)
        await browser.close()

    updated = unknown = 0
    for row, info in zip(targets, results):
        if info["has_exam"] is None:
            unknown += 1
            continue
        row["has_test"]    = "1" if info["has_exam"] else "0"
        row["exam_date_a"] = info["date_a"]
        row["exam_date_b"] = info["date_b"]
        updated += 1

    log(f"Updated {updated} courses ({unknown} could not be determined)")

    with open(LABELED_CSV, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    log(f"Wrote {LABELED_CSV}")

    if os.path.exists(os.path.dirname(UI_PUBLIC)):
        shutil.copy(LABELED_CSV, UI_PUBLIC)
        log(f"Copied to {UI_PUBLIC}")

if __name__ == "__main__":
    asyncio.run(main())
