"""
patch_ratings.py  —  re-fetch CheeseFork ratings, loop until stable
"""
import asyncio, csv, os, re
import aiohttp
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

OUTPUT_DIR  = "C:/Users/pc/OneDrive - Technion/Desktop/cheesefork_scraper"
AGG_CSV     = os.path.join(OUTPUT_DIR, "courses_aggregated_all.csv")
PER_SEM_CSV = os.path.join(OUTPUT_DIR, "courses_per_semester_all.csv")
BASE        = "https://cheesefork.cf/"

# ── Firestore helpers ──────────────────────────────────────────────────────────
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

async def get_firebase_config(pw_page):
    """Use Playwright so JS executes and we get the real runtime config."""
    await pw_page.goto(BASE, wait_until="networkidle", timeout=60000)
    cfg = await pw_page.evaluate("""() => {
        if (window.firebaseConfig) return window.firebaseConfig;
        try { const apps = firebase.apps; if (apps && apps.length) return apps[0].options; } catch(e) {}
        return null;
    }""")
    if cfg and cfg.get("apiKey") and cfg.get("projectId"):
        return cfg["apiKey"], cfg["projectId"]
    html = await pw_page.content()
    m1 = re.search(r'apiKey["\']?\s*:\s*["\']([^"\']+)["\']', html)
    m2 = re.search(r'projectId["\']?\s*:\s*["\']([^"\']+)["\']', html)
    if m1 and m2: return m1.group(1), m2.group(1)
    if m1: return m1.group(1), "cheesefork-de9af"
    raise RuntimeError("Firebase config not found")

async def fetch_ratings(session, api_key, project_id, course_id):
    url = (f"https://firestore.googleapis.com/v1/projects/{project_id}/databases/(default)"
           f"/documents/courseFeedback/{course_id}?key={api_key}")
    out = {"avg_general": None, "n_general": 0}
    try:
        async with session.get(url, timeout=30) as r:
            if r.status != 200:
                return out
            text = await r.text()
        import json
        doc = json.loads(text)
    except Exception as e:
        if course_id in ("00140013", "02760413"):
            print(f"  [fetch_err] {course_id}: {type(e).__name__}: {e}")
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

def fmt(v, d=3):
    return f"{v:.{d}f}" if v is not None else ""

# ── Single pass ────────────────────────────────────────────────────────────────
async def run_pass(pass_num):
    # Load current CSV state
    with open(AGG_CSV, encoding="utf-8-sig") as f:
        agg_rows = list(csv.DictReader(f))
    with open(PER_SEM_CSV, encoding="utf-8-sig") as f:
        per_sem_rows = list(csv.DictReader(f))

    # Which courses still need ratings?
    todo = [r["course_id"] for r in agg_rows if int(r.get("n_general_rank") or 0) == 0]
    have = len(agg_rows) - len(todo)
    print(f"  {have}/{len(agg_rows)} courses already have ratings.")
    print(f"  Fetching {len(todo)} courses with 0 reviews...")

    if not todo:
        return 0

    # Quick sanity check before full run
    print("  Running sanity check on course 02760413...")

    # Get Firebase config fresh each pass via Playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page    = await browser.new_page()
        api_key, project_id = await get_firebase_config(page)
        print(f"  apiKey={api_key[:10]}...  projectId={project_id}")
        await browser.close()

    # Sanity check: manually fetch a known course
    import aiohttp as _aio
    async with _aio.ClientSession() as _s:
        _url = (f"https://firestore.googleapis.com/v1/projects/{project_id}/databases/(default)"
                f"/documents/courseFeedback/02760413?key={api_key}")
        async with _s.get(_url, timeout=30) as _r:
            _j = await _r.json()
            _posts = (_j.get("fields",{}).get("posts",{}).get("arrayValue",{}).get("values") or [])
            print(f"  [sanity] status={_r.status} posts={len(_posts)} (expected 7)")
            if _r.status != 200:
                print(f"  [sanity] ERROR body: {str(_j)[:300]}")

    # Fetch missing ratings
    new_ratings = {}
    async with aiohttp.ClientSession(headers={"User-Agent": "PersonalCourseAnalytics/1.0"}) as session:
        for i, cid in enumerate(todo, 1):
            new_ratings[cid] = await fetch_ratings(session, api_key, project_id, cid)
            if i == 1:
                print(f"  [debug first course] {cid} -> {new_ratings[cid]}")
                # Also print raw response for this course
                import aiohttp as _dbg_aio, json as _dbg_json
                async with _dbg_aio.ClientSession() as _dbg_s:
                    _dbg_url = (f"https://firestore.googleapis.com/v1/projects/{project_id}/databases/(default)"
                                f"/documents/courseFeedback/{cid}?key={api_key}")
                    async with _dbg_s.get(_dbg_url, timeout=30) as _dbg_r:
                        _dbg_text = await _dbg_r.text()
                        print(f"  [raw response {cid}] status={_dbg_r.status} body={_dbg_text[:300]}")
            if i % 100 == 0 or i == len(todo):
                print(f"  [{i}/{len(todo)}] fetched...")

    # Count improvements
    improved = sum(1 for cid, r in new_ratings.items() if r["n_general"] > 0)
    print(f"  Found ratings for {improved}/{len(todo)} previously-zero courses.")

    # Patch aggregated CSV
    ratings_map = {r["course_id"]: {"avg_general": float(r["avg_general_rank"]) if r.get("avg_general_rank") else None,
                                     "n_general": int(r.get("n_general_rank") or 0)}
                   for r in agg_rows}
    ratings_map.update(new_ratings)

    agg_fieldnames = list(agg_rows[0].keys())
    for r in agg_rows:
        nr = ratings_map[r["course_id"]]
        r["avg_general_rank"] = fmt(nr["avg_general"], 3) if nr["avg_general"] is not None else ""
        r["n_general_rank"]   = nr["n_general"]

    with open(AGG_CSV, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=agg_fieldnames)
        w.writeheader(); w.writerows(agg_rows)

    # Patch per-semester CSV
    per_sem_fieldnames = list(per_sem_rows[0].keys())
    for r in per_sem_rows:
        nr = ratings_map.get(r["course_id"], {"avg_general": None, "n_general": 0})
        r["avg_general_rank"] = fmt(nr["avg_general"], 3) if nr["avg_general"] is not None else ""
        r["n_general_rank"]   = nr["n_general"]

    with open(PER_SEM_CSV, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=per_sem_fieldnames)
        w.writeheader(); w.writerows(per_sem_rows)

    print(f"  CSVs updated.")
    return improved

# ── Loop until stable ──────────────────────────────────────────────────────────
async def run_until_stable():
    pass_num = 1
    while True:
        print(f"\n{'='*60}")
        print(f"  PASS {pass_num}")
        print(f"{'='*60}")
        improved = await run_pass(pass_num)
        if improved == 0:
            print(f"\nNo new ratings found in pass {pass_num} — all done!")
            break
        print(f"\nPass {pass_num}: filled in {improved} courses. Running again...")
        pass_num += 1

asyncio.run(run_until_stable())