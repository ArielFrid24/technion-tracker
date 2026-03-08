"""
update_latest.py
Step 1: Find the latest semester on CheeseFork and save its course list.

Outputs: semester_<CODE>.json  e.g. semester_202502.json
         (skips if file already exists and --force not passed)

Usage:
    python update_latest.py           # auto-detect latest semester
    python update_latest.py --force   # re-scrape even if file exists
"""
import asyncio, re, json, os, sys
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

BASE       = "https://cheesefork.cf/"
OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))

def extract_codes(text):
    return set(re.findall(r"[?&]course=(\d{6,8})\b", text))

async def get_latest_semester(page):
    """Load CheeseFork homepage and find the newest semester code."""
    print("Loading CheeseFork to find latest semester...")
    await page.goto(BASE, wait_until="domcontentloaded", timeout=60000)
    try: await page.wait_for_load_state("networkidle", timeout=10000)
    except PlaywrightTimeout: pass

    html = await page.content()

    # Semester codes appear as option values in the dropdown
    semesters = set(re.findall(r'\b(20\d{4})\b', html))
    semesters = [s for s in semesters if s[4:] in ("01", "02", "03")]

    if not semesters:
        raise RuntimeError("Could not find any semester codes on CheeseFork")

    latest = max(semesters)
    print(f"Found semesters: {sorted(semesters)}")
    print(f"Latest: {latest}")
    return latest

async def scrape_semester_courses(page, semester):
    """Scrape all course IDs offered in a given semester."""
    url = f"{BASE}?course=all&semester={semester}"
    print(f"Scraping {url} ...")

    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    try: await page.wait_for_load_state("networkidle", timeout=10000)
    except PlaywrightTimeout: pass

    html  = await page.content()
    codes = extract_codes(html)

    try:
        hrefs = await page.eval_on_selector_all(
            "a[href]", "els => els.map(e => e.getAttribute('href')).filter(Boolean)"
        )
        codes |= extract_codes("\n".join(hrefs))
    except: pass

    return sorted({c.zfill(8) for c in codes})

async def main():
    force = "--force" in sys.argv

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page    = await (await browser.new_context()).new_page()

        try:
            latest = await get_latest_semester(page)
        except Exception as e:
            print(f"Error detecting semester: {e}")
            await browser.close()
            sys.exit(1)

        out_path = os.path.join(OUTPUT_DIR, f"semester_{latest}.json")

        if os.path.exists(out_path) and not force:
            with open(out_path, encoding="utf-8") as f:
                existing = json.load(f)
            print(f"Already have semester_{latest}.json ({len(existing['courses'])} courses) — use --force to re-scrape")
            await browser.close()
            return

        try:
            codes = await scrape_semester_courses(page, latest)
        except Exception as e:
            print(f"Error scraping courses: {e}")
            await browser.close()
            sys.exit(1)

        await browser.close()

    if not codes:
        print("No courses found — something went wrong")
        sys.exit(1)

    data = {"semester": latest, "courses": codes}
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\nDone. Saved {len(codes)} courses for semester {latest} -> {out_path}")

asyncio.run(main())