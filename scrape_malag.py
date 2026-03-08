"""
scrape_malag.py
Scrapes מלג courses from Technion portal (all pages of the table),
appends to courses_labeled.csv with category=מלג.
"""
import asyncio, csv, os, re
from playwright.async_api import async_playwright

OUTPUT_DIR  = "C:/Users/pc/OneDrive - Technion/Desktop/cheesefork_scraper"
LABELED_CSV = os.path.join(OUTPUT_DIR, "courses_labeled.csv")
AGG_CSV     = os.path.join(OUTPUT_DIR, "courses_aggregated_all.csv")
PORTAL_URL  = "https://ugportal.technion.ac.il/הוראה-ובחינות/לימודי-העשרה/"

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

async def scrape_table_page(container):
    """Scrape current visible page of the table within a container element."""
    courses = []
    rows = await container.query_selector_all("table tbody tr")
    for row in rows:
        cells = await row.query_selector_all("td")
        if len(cells) < 2:
            continue
        texts = [(await c.inner_text()).strip() for c in cells]
        cid_raw = texts[0]
        if not re.match(r"^\d{6,8}$", cid_raw):
            continue
        cid = cid_raw.zfill(8)
        name_he = texts[1] if len(texts) > 1 else ""
        name_en = texts[2] if len(texts) > 2 else ""
        courses.append((cid, name_he, name_en))
    return courses

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

async def get_malag_courses(page):
    print(f"Loading portal...")
    await page.goto(PORTAL_URL, wait_until="domcontentloaded", timeout=30000)
    try: await page.wait_for_load_state("networkidle", timeout=10000)
    except: pass

    # Find the FIRST accordion toggle (latest semester) and its parent container
    toggles = await page.query_selector_all(
        ".et_pb_toggle_title, .accordion-title, summary, "
        "[class*='accordion'] h3, [class*='accordion'] h4, "
        "[class*='toggle'] h3, [class*='toggle'] h4"
    )

    target = None
    for t in toggles:
        txt = (await t.inner_text()).strip()
        if "רשימת" in txt or "סמסטר" in txt:
            print(f"Clicking: {txt[:80]}")
            target = t
            break

    if not target:
        raise RuntimeError("Could not find semester accordion toggle")

    # Get the accordion container BEFORE clicking so we can scope all queries to it
    # Walk up to find the accordion wrapper element
    container = await target.evaluate_handle("""el => {
        let node = el;
        while (node && node !== document.body) {
            node = node.parentElement;
            if (node.classList && (
                Array.from(node.classList).some(c =>
                    c.includes('toggle') || c.includes('accordion') || c.includes('et_pb')
                )
            )) return node;
        }
        return el.parentElement;
    }""")

    await target.click()
    await page.wait_for_timeout(2000)

    # Wait for table inside our container
    try:
        await page.wait_for_selector("table tbody tr", timeout=8000)
    except:
        raise RuntimeError("Table did not appear after clicking accordion")

    all_courses = []
    page_num = 1

    while True:
        courses = await scrape_table_page(container)
        print(f"  Page {page_num}: {len(courses)} courses")
        all_courses.extend(courses)

        # Find next page button scoped to our container
        next_btn = None
        nav_links = await container.query_selector_all("a, button")
        for link in nav_links:
            txt = (await link.inner_text()).strip()
            cls = (await link.get_attribute("class") or "")
            if txt in ("›", ">", "Next", "»") or "next" in cls.lower():
                try:
                    is_disabled = await link.is_disabled()
                    aria_disabled = await link.get_attribute("aria-disabled")
                    tabindex = await link.get_attribute("tabindex")
                    if not is_disabled and aria_disabled != "true" and tabindex != "-1":
                        next_btn = link
                        break
                except:
                    continue

        if not next_btn:
            print(f"  No more pages after page {page_num}")
            break

        try:
            await next_btn.click(timeout=5000)
        except:
            print(f"  Next button not clickable, stopping at page {page_num}")
            break
        await page.wait_for_timeout(1500)
        page_num += 1

        if page_num > 10:
            break

    return all_courses

async def main():
    existing_rows, cols = load_labeled()
    agg = load_agg()

    if "semester" not in cols:
        cols = cols + ["semester"]

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page    = await (await browser.new_context()).new_page()
        malag_courses = await get_malag_courses(page)
        await browser.close()

    print(f"\nTotal scraped: {len(malag_courses)} מלג courses")

    # Deduplicate by course_id (keep first occurrence)
    seen = set()
    unique_courses = []
    for cid, name_he, name_en in malag_courses:
        if cid not in seen:
            seen.add(cid)
            unique_courses.append((cid, name_he, name_en))

    print(f"Unique course IDs: {len(unique_courses)}")

    new_rows = []
    print(f"\nScraping CheeseFork for credits & prereqs for {len(unique_courses)} מלג courses...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page2   = await (await browser.new_context()).new_page()
        for i, (cid, name_he, name_en) in enumerate(unique_courses, 1):
            ex = agg.get(cid, {})
            name = ex.get("course_name", "") or name_he
            print(f"  [{i}/{len(unique_courses)}] {cid}", end="  ")
            name_scraped, credits, prereqs = await scrape(page2, cid)
            if not name: name = name_scraped or name_he
            final_credits = credits if credits is not None else 2
            print(f"credits={final_credits}  prereqs={prereqs or '-'}  {name[:30]}")
            new_rows.append({
                "course_id":        cid,
                "course_name":      name,
                "category":         "מלג",
                "credits":          final_credits,
                "prereqs":          prereqs,
                "avg_final_grade":  ex.get("avg_final_grade", ""),
                "avg_general_rank": ex.get("avg_general_rank", ""),
                "n_general_rank":   ex.get("n_general_rank", ""),
                "semester":         "",
            })
        await browser.close()

    # Replace old מלג rows with fresh ones
    existing_rows = [r for r in existing_rows if r.get("category") != "מלג"]
    all_rows = existing_rows + new_rows

    with open(LABELED_CSV, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(all_rows)

    print(f"\nSaved {len(all_rows)} total rows -> {LABELED_CSV}")
    print(f"  מלג: {len(new_rows)}")

asyncio.run(main())