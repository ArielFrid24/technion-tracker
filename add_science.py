"""
add_science.py
Scrapes credits, prereqs from CheeseFork for קורסי מדעי,
joins grades from courses_aggregated_all.csv,
appends to courses_labeled.csv with category=קורס מדעי.
"""
import asyncio, csv, os, re
from playwright.async_api import async_playwright

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

async def scrape(page, course_id):
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
                raw = re.sub(r"\u05d5-", "\u05d5 ", raw)
                raw = re.sub(r"\u05d0\u05d5-", "\u05d0\u05d5 ", raw)
                raw = raw.replace("(", "").replace(")", "")
                parts = []
                for t in raw.split():
                    t = t.strip().rstrip("-")
                    if re.match(r"^\d{6,8}$", t):
                        parts.append(t.zfill(8))
                    elif t == "\u05d0\u05d5":
                        parts.append("OR")
                    elif t == "\u05d5":
                        parts.append("AND")
                prereq_str = " ".join(parts)

            if credits is not None or name:
                return name, credits, prereq_str
        except:
            continue
    return "", None, ""

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

        for i, cid in enumerate(SCIENCE_COURSES, 1):
            ex = agg.get(cid, {})
            name_csv = ex.get("course_name", "")
            print(f"[{i}/{len(SCIENCE_COURSES)}] {cid}", end="  ")

            name_scraped, credits, prereqs = await scrape(page, cid)
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