"""
scraper_common.py
Shared scraping primitives used by update_all.py and the standalone
label scripts (Mendatory_course_scraper.py, scrape_malag.py, add_science.py,
Scrape_free_choice.py, scraper_for_recommended.py).

Consolidates what used to be 6 independent copies of the same CheeseFork
course-detail scraper (each with its own hardcoded, slowly-rotting semester
fallback list) into one implementation that discovers real semester codes
from CheeseFork at run time.
"""
import asyncio, re
from playwright.async_api import TimeoutError as PlaywrightTimeout

CF_BASE    = "https://cheesefork.cf/"
HIST_BASE  = "https://michael-maltsev.github.io/technion-histograms"
CF_API_KEY = "AIzaSyAfKPyTM83mkLgdQTdx9YS9UXywiswwIYI"
CF_PROJECT = "cheesefork-de9af"

# ── Semester labels ────────────────────────────────────────────────────────────
def sem_to_label(sem):
    y, s = int(sem[:4]), sem[4:]
    if s == "01": return f"חורף {y}-{y+1}"
    if s == "02": return f"אביב {y+1}"
    if s == "03": return f"קיץ {y+1}"
    return sem

# ── Semester discovery (replaces every hardcoded fallback tuple) ──────────────
async def discover_semesters(page):
    """Return all semester codes CheeseFork knows about, sorted ascending."""
    await page.goto(CF_BASE, wait_until="domcontentloaded", timeout=60000)
    try: await page.wait_for_load_state("networkidle", timeout=10000)
    except PlaywrightTimeout: pass
    html = await page.content()
    sems = sorted({s for s in re.findall(r'\b(20\d{4})\b', html) if s[4:] in ("01", "02", "03")})
    return sems

# ── Firebase / Firestore ───────────────────────────────────────────────────────
_APIKEY_RE    = re.compile(r"""apiKey["']?\s*:\s*["']([^"']+)["']""")
_PROJECTID_RE = re.compile(r"""projectId["']?\s*:\s*["']([^"']+)["']""")

async def get_firebase_config(session):
    """Scan CheeseFork's homepage + linked scripts for the Firebase apiKey/projectId."""
    async with session.get(CF_BASE, timeout=30) as r:
        html = await r.text()
    m1, m2 = _APIKEY_RE.search(html), _PROJECTID_RE.search(html)
    if m1 and m2: return m1.group(1), m2.group(1)
    for src in re.findall(r"""<script[^>]+src=["']([^"']+)["']""", html, re.IGNORECASE):
        url = src if src.startswith("http") else CF_BASE.rstrip("/") + ("" if src.startswith("/") else "/") + src
        try:
            async with session.get(url, timeout=30) as r:
                js = await r.text()
            m1, m2 = _APIKEY_RE.search(js), _PROJECTID_RE.search(js)
            if m1 and m2: return m1.group(1), m2.group(1)
        except Exception:
            continue
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
            except Exception: return None
    return None

async def fetch_ratings(session, api_key, project_id, course_id):
    url = (f"https://firestore.googleapis.com/v1/projects/{project_id}/databases/(default)"
           f"/documents/courseFeedback/{course_id}?key={api_key}")
    out = {"avg_general": None, "n_general": 0}
    try:
        async with session.get(url, timeout=30) as r:
            if r.status != 200: return out
            doc = await r.json()
    except Exception:
        return out
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

# ── Histogram (grade table) parsing ────────────────────────────────────────────
def parse_finals_table(window):
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
    except (ValueError, IndexError):
        return None

def extract_finals_for_sem(html, sem):
    m = re.search(rf'id="{re.escape(sem)}-Finals?"', html, re.IGNORECASE)
    if m:
        r = parse_finals_table(html[m.start():m.start() + 2000])
        if r: return r
    parts = []
    for m in re.finditer(rf'id="({re.escape(sem)}-Final_[^"]+)"', html, re.IGNORECASE):
        r = parse_finals_table(html[m.start():m.start() + 2000])
        if r: parts.append(r)
    if not parts: return None
    total_n = sum(p["students"] for p in parts)
    pass_total = sum(p["pass_n"] for p in parts if p["pass_n"] is not None)
    return {
        "students": total_n, "pass_n": pass_total,
        "fail_n":    sum(p["fail_n"] for p in parts if p["fail_n"] is not None),
        "pass_pct":  round(100 * pass_total / total_n, 1) if total_n else None,
        "min_grade": min(p["min_grade"] for p in parts if p["min_grade"] is not None),
        "max_grade": max(p["max_grade"] for p in parts if p["max_grade"] is not None),
        "avg_grade": sum(p["avg_grade"] * p["students"] for p in parts) / total_n,
        "median_grade": None,
    }

async def fetch_hist_all_semesters(page, course_id):
    """Load a course's histogram page and return {semester: finals_dict} for every semester found."""
    try:
        await page.goto(f"{HIST_BASE}/{course_id}/", wait_until="domcontentloaded", timeout=60000)
        try: await page.wait_for_selector("text=סופי", timeout=8000)
        except PlaywrightTimeout: pass
        await page.wait_for_timeout(800)
        html = await page.content()
    except Exception:
        return {}
    all_sems = sorted(set(re.findall(r'id="(\d{6})-[Ff]inal', html)))
    results = {}
    for sem in all_sems:
        r = extract_finals_for_sem(html, sem)
        if r: results[sem] = r
    return results

# ── CheeseFork course-detail scraper (the canonical, de-duplicated version) ───
async def scrape_cf_course(page, course_id, semester_fallback):
    """
    Get (name, credits, prereq_str) for a course from CheeseFork's detail page.
    semester_fallback: iterable of semester codes to try, most-recent-first
    (pass the result of discover_semesters(), reversed, from the caller).
    """
    for sem in semester_fallback:
        try:
            await page.goto(f"{CF_BASE}?course={course_id}&semester={sem}",
                            wait_until="domcontentloaded", timeout=20000)
            try: await page.wait_for_selector("text=נקודות", timeout=3000)
            except PlaywrightTimeout: pass
            try: await page.wait_for_load_state("networkidle", timeout=3000)
            except PlaywrightTimeout: pass

            title = await page.title()
            if course_id.lstrip("0") not in title and course_id not in title:
                continue

            name = ""
            m = re.match(r"^\s*[\d\s]*-\s*(.+?)\s*-", title)
            if m: name = m.group(1).strip()

            body = await page.inner_text("body")
            cm = re.search(r"נקודות[^:]*:\s*(\d+(?:\.\d+)?)", body)
            credits = float(cm.group(1)) if cm and float(cm.group(1)) <= 20 else None

            prereq_str = ""
            pm = re.search(r"מקצועות קדם[:\s]+([\dאוו \(\)-]+)", body)
            if pm:
                raw = pm.group(1).strip()
                raw = re.sub(r"ו-", "ו ", raw)
                raw = re.sub(r"או-", "או ", raw)
                raw = raw.replace("(", "").replace(")", "")
                parts = []
                for t in raw.split():
                    t = t.strip().rstrip("-")
                    if re.match(r"^\d{6,8}$", t): parts.append(t.zfill(8))
                    elif t == "או": parts.append("OR")
                    elif t == "ו": parts.append("AND")
                prereq_str = " ".join(parts)

            if credits is not None or name:
                return name, credits, prereq_str
        except Exception:
            continue
    return "", None, ""

# ── Bounded-concurrency worker pool ────────────────────────────────────────────
async def run_pooled(browser, items, worker, concurrency=8, on_progress=None):
    """
    Run `await worker(page, item)` over `items` using a fixed pool of
    `concurrency` pages (borrowed/returned via a queue), returning results
    in input order. Replaces the fully-sequential per-course loops that made
    a full grades/credits re-check take 60-90+ minutes.

    on_progress(completed, total), if given, is called after every item
    finishes — pass something that logs periodically so a long run redirected
    to a file doesn't look hung (Python fully buffers stdout when it isn't a
    tty, so without this + a caller that flushes, nothing appears for a long
    time even while work is actively progressing).
    """
    if not items:
        return []
    n = min(concurrency, len(items))
    contexts = [await browser.new_context() for _ in range(n)]
    queue = asyncio.Queue()
    for ctx in contexts:
        queue.put_nowait(await ctx.new_page())

    results = [None] * len(items)
    completed = 0

    async def run_one(i, item):
        nonlocal completed
        page = await queue.get()
        try:
            results[i] = await worker(page, item)
        finally:
            queue.put_nowait(page)
            completed += 1
            if on_progress:
                on_progress(completed, len(items))

    await asyncio.gather(*(run_one(i, item) for i, item in enumerate(items)))

    for ctx in contexts:
        await ctx.close()
    return results
