import asyncio
import os
import csv
import re
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

BASE      = "https://cheesefork.cf/"
HIST_BASE = "https://michael-maltsev.github.io/technion-histograms"
MAX_COURSES = 9999
OUTPUT_DIR  = "C:/Users/pc/OneDrive - Technion/Desktop/cheesefork_scraper"

def extract_course_codes(text):
    return sorted(set(re.findall(r"[?&]course=(\d{6,8})\b", text)))

def parse_course_title(title):
    t = title.replace("\u2013", "-").replace("\u2014", "-").strip()
    m = re.match(r"^\s*(\d{6,8})\s*-\s*(.+?)\s*-\s*.+$", t)
    if m: return m.group(1), m.group(2).strip()
    m2 = re.match(r"^\s*(\d{6,8})\s*-\s*(.+)$", t)
    if m2: return m2.group(1), m2.group(2).strip()
    return "", ""

def sem_to_label(sem):
    year, s = int(sem[:4]), sem[4:]
    if s == "01": return f"\u05d7\u05d5\u05e8\u05e3 {year}-{year+1}"
    if s == "02": return f"\u05d0\u05d1\u05d9\u05d1 {year}"
    if s == "03": return f"\u05e7\u05d9\u05e5 {year}"
    return sem

_APIKEY_RE    = re.compile(r"""apiKey["']?\s*:\s*["']([^"']+)["']""")
_PROJECTID_RE = re.compile(r"""projectId["']?\s*:\s*["']([^"']+)["']""")

async def get_firebase_config(session):
    async with session.get(BASE, timeout=30) as r:
        html = await r.text()
    m1, m2 = _APIKEY_RE.search(html), _PROJECTID_RE.search(html)
    if m1 and m2: return m1.group(1), m2.group(1)
    for src in re.findall(r"""<script[^>]+src=["']([^"']+)["']""", html, re.IGNORECASE):
        url = src if src.startswith("http") else BASE.rstrip("/") + ("" if src.startswith("/") else "/") + src
        try:
            async with session.get(url, timeout=30) as r:
                js = await r.text()
            m1, m2 = _APIKEY_RE.search(js), _PROJECTID_RE.search(js)
            if m1 and m2: return m1.group(1), m2.group(1)
        except: continue
    raise RuntimeError("Firebase config not found")

def _fs_get(obj, path):
    for p in path:
        if not isinstance(obj, dict) or p not in obj: return None
        obj = obj[p]
    return obj

def _fs_num(v):
    if not isinstance(v, dict): return None
    for k in ("integerValue", "doubleValue"):
        if k in v:
            try: return float(v[k])
            except: return None
    return None

async def fetch_ratings(session, api_key, project_id, course_id):
    url = (f"https://firestore.googleapis.com/v1/projects/{project_id}/databases/(default)"
           f"/documents/courseFeedback/{course_id}?key={api_key}")
    out = {"avg_general": None, "n_general": 0}
    try:
        async with session.get(url, timeout=30) as r:
            if r.status != 200: return out
            doc = await r.json()
    except: return out
    posts = _fs_get(doc, ["fields", "posts", "arrayValue", "values"])
    if not isinstance(posts, list): return out
    vals = []
    for it in posts:
        fields = _fs_get(it, ["mapValue", "fields"])
        if not isinstance(fields, dict): continue
        g = _fs_num(fields.get("generalRank"))
        if g is not None: vals.append(g)
    if vals:
        out["avg_general"] = sum(vals) / len(vals)
        out["n_general"]   = len(vals)
    return out

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

async def fetch_hist_all_semesters(page, course_id):
    try:
        await page.goto(f"{HIST_BASE}/{course_id}/", wait_until="domcontentloaded", timeout=60000)
        try: await page.wait_for_selector("text=\u05e1\u05d5\u05e4\u05d9", timeout=12000)
        except PlaywrightTimeoutError: pass
        await page.wait_for_timeout(1500)
        html = await page.content()
    except Exception as e:
        print(f"  [hist] Error {course_id}: {e}"); return {}
    all_sems = sorted(set(re.findall(r'id="(\d{6})-[Ff]inal', html)))
    results = {}
    for sem in all_sems:
        r = _extract_finals_for_sem(html, sem)
        if r: results[sem] = r
    if results:
        print(f"  [hist] {course_id}: {len(results)} semesters ({min(results.keys())} to {max(results.keys())})")
    else:
        print(f"  [hist] {course_id}: no Finals data found")
    return results

def pearson(xs, ys):
    if len(xs) != len(ys) or len(xs) < 2: return None
    mx, my = sum(xs)/len(xs), sum(ys)/len(ys)
    num = sum((x-mx)*(y-my) for x,y in zip(xs,ys))
    den = (sum((x-mx)**2 for x in xs) * sum((y-my)**2 for y in ys)) ** 0.5
    return None if den == 0 else num/den

def rankdata(vals):
    si = sorted(range(len(vals)), key=lambda i: vals[i])
    ranks = [0.0]*len(vals); i = 0
    while i < len(vals):
        j = i
        while j+1 < len(vals) and vals[si[j+1]] == vals[si[i]]: j += 1
        r = (i+j)/2.0+1.0
        for k in range(i,j+1): ranks[si[k]] = r
        i = j+1
    return ranks

def spearman(xs, ys):
    return pearson(rankdata(xs), rankdata(ys)) if len(xs)==len(ys) and len(xs)>=2 else None

def fmt(v, d=3):
    return f"{v:.{d}f}" if v is not None else ""

async def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    csv_per_sem = os.path.join(OUTPUT_DIR, "courses_per_semester_all.csv")
    csv_agg     = os.path.join(OUTPUT_DIR, "courses_aggregated_all.csv")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx  = await browser.new_context()
        page = await ctx.new_page()

        # Step 1: discover all available semesters from the dropdown
        for attempt in range(3):
            try:
                await page.goto(BASE, wait_until="domcontentloaded", timeout=60000)
                break
            except PlaywrightTimeoutError:
                print(f"  Timeout loading main page (attempt {attempt+1}/3), retrying...")
                await asyncio.sleep(3)
        try: await page.wait_for_load_state("networkidle", timeout=15000)
        except PlaywrightTimeoutError: pass

        main_html = await page.content()
        # Semester codes appear as option values or in links, e.g. value="202401"
        all_semesters = sorted(set(re.findall(r'\b(20\d{4})\b', main_html)))
        # Filter to plausible semester codes: last 2 digits 01/02/03
        all_semesters = [s for s in all_semesters if s[4:] in ("01","02","03")]
        if not all_semesters:
            all_semesters = ["202401"]  # fallback
        print(f"Found {len(all_semesters)} semesters: {all_semesters[0]} to {all_semesters[-1]}")

        # Step 2: collect course codes from every semester's course list
        all_codes: set = set()
        # Track which semester each course was last seen in (for name lookup)
        course_last_sem: dict = {}
        for sem in all_semesters:
            url = f"{BASE}?course=all&semester={sem}"
            for attempt in range(2):
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    break
                except PlaywrightTimeoutError:
                    await asyncio.sleep(2)
            try: await page.wait_for_load_state("networkidle", timeout=10000)
            except PlaywrightTimeoutError: pass
            html = await page.content()
            sem_codes = set(extract_course_codes(html))
            if not sem_codes:
                hrefs = await page.eval_on_selector_all("a[href]", "els => els.map(e=>e.getAttribute('href')).filter(Boolean)")
                sem_codes = set(extract_course_codes("\n".join(hrefs)))
            new_codes = sem_codes - all_codes
            all_codes |= sem_codes
            for c in sem_codes:
                course_last_sem[c] = sem
            print(f"  sem={sem}: {len(sem_codes)} courses ({len(new_codes)} new, {len(all_codes)} total)")

        codes = sorted(all_codes)[:MAX_COURSES]
        print(f"\nTotal unique courses discovered: {len(sorted(all_codes))} -> using {len(codes)}")

        names = {}
        for code in codes:
            sem = course_last_sem.get(code, "202401")
            try:
                await page.goto(f"{BASE}?course={code}&semester={sem}",
                                wait_until="domcontentloaded", timeout=60000)
                _, n = parse_course_title(await page.title())
                names[code] = n or ""
            except: names[code] = ""

        hist_page = await ctx.new_page()

        async with aiohttp.ClientSession(headers={"User-Agent": "PersonalCourseAnalytics/1.0"}) as session:
            api_key, project_id = await get_firebase_config(session)
            per_sem_rows, agg_rows = [], []

            for i, code in enumerate(codes, 1):
                name = names.get(code, "")
                print(f"[{i}/{len(codes)}] {code}  ({name})")

                hist    = await fetch_hist_all_semesters(hist_page, code)
                ratings = await fetch_ratings(session, api_key, project_id, code)

                for sem in sorted(hist.keys()):
                    h = hist[sem]
                    per_sem_rows.append({
                        "course_id": code, "course_name": name,
                        "semester": sem, "semester_label": sem_to_label(sem),
                        "students": h["students"], "pass_n": h["pass_n"],
                        "fail_n": h["fail_n"], "pass_pct": h["pass_pct"],
                        "min_grade": h["min_grade"], "max_grade": h["max_grade"],
                        "avg_grade": h["avg_grade"], "median_grade": h["median_grade"],
                        "avg_general_rank": ratings["avg_general"],
                        "n_general_rank":   ratings["n_general"],
                        "hist_url": f"{HIST_BASE}/{code}/",
                        "cf_url":   f"{BASE}?course={code}&semester={sem}",
                    })

                hparts  = [h for h in hist.values() if h.get("avg_grade") is not None]
                total_n = sum(h["students"] for h in hparts) if hparts else None
                agg_grade = (sum(h["avg_grade"]*h["students"] for h in hparts)/total_n
                             if hparts and total_n else None)
                agg_pass  = (sum(h["pass_pct"]*h["students"] for h in hparts
                                 if h.get("pass_pct") is not None)/total_n
                             if hparts and total_n else None)

                agg_rows.append({
                    "course_id": code, "course_name": name,
                    "semester_range": f"{min(hist.keys())} to {max(hist.keys())}" if hist else "",
                    "n_semesters": len(hist),
                    "avg_final_grade": agg_grade, "total_students": total_n,
                    "avg_pass_pct": agg_pass,
                    "avg_general_rank": ratings["avg_general"],
                    "n_general_rank":   ratings["n_general"],
                    "hist_url": f"{HIST_BASE}/{code}/",
                    "cf_url":   f"{BASE}?course={code}&semester={course_last_sem.get(code, '202401')}",
                })
                print(f"  -> grade={fmt(agg_grade,2)} ({len(hist)} sems, n={total_n})  "
                      f"general={fmt(ratings['avg_general'],2)} ({ratings['n_general']} reviews)")

        await browser.close()

    PER_SEM_COLS = ["course_id","course_name","semester","semester_label",
                    "students","pass_n","fail_n","pass_pct",
                    "min_grade","max_grade","avg_grade","median_grade",
                    "avg_general_rank","n_general_rank","hist_url","cf_url"]
    with open(csv_per_sem, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=PER_SEM_COLS)
        w.writeheader()
        for r in per_sem_rows:
            w.writerow({k: (fmt(r[k]) if isinstance(r[k], float) else ("" if r[k] is None else r[k])) for k in PER_SEM_COLS})

    AGG_COLS = ["course_id","course_name","semester_range","n_semesters",
                "avg_final_grade","total_students","avg_pass_pct",
                "avg_general_rank","n_general_rank","hist_url","cf_url"]
    with open(csv_agg, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=AGG_COLS)
        w.writeheader()
        for r in agg_rows:
            w.writerow({k: (fmt(r[k]) if isinstance(r[k], float) else ("" if r[k] is None else r[k])) for k in AGG_COLS})

    print(f"\nSaved: {csv_per_sem}  ({len(per_sem_rows)} rows)")
    print(f"Saved: {csv_agg}  ({len(agg_rows)} rows)")

    paired = [r for r in agg_rows if r["avg_final_grade"] is not None and r["avg_general_rank"] is not None]
    xs = [r["avg_final_grade"]  for r in paired]
    ys = [r["avg_general_rank"] for r in paired]
    print(f"\nCorrelation on {len(paired)} courses (avg_final_grade vs avg_general_rank):")
    print(f"  Pearson  = {fmt(pearson(xs, ys), 4)}")
    print(f"  Spearman = {fmt(spearman(xs, ys), 4)}")

    ranked = sorted([r for r in agg_rows if r["avg_general_rank"] is not None],
                    key=lambda r: r["avg_general_rank"])
    if ranked:
        print(f"\nWorst rated: {ranked[0]['course_id']} {ranked[0]['course_name']}"
              f"  (rank={ranked[0]['avg_general_rank']:.2f}, grade={fmt(ranked[0]['avg_final_grade'],1)})")
        print(f"Best rated:  {ranked[-1]['course_id']} {ranked[-1]['course_name']}"
              f"  (rank={ranked[-1]['avg_general_rank']:.2f}, grade={fmt(ranked[-1]['avg_final_grade'],1)})")

asyncio.run(main())