"""
app.py — Technion course recommender backend
"""
import csv, glob, io, json, os, re, shutil, subprocess, tempfile, urllib.request, zipfile
from flask import Flask, jsonify, request
from flask_cors import CORS

OUTPUT_DIR  = os.path.dirname(os.path.abspath(__file__))
LABELED_CSV = os.path.join(OUTPUT_DIR, "courses_labeled.csv")

# Most users get the app via GitHub's "Download ZIP" button, not git clone,
# so an update can't just be `git pull` — it downloads the latest branch
# zip straight from GitHub and copies it over the local install.
GITHUB_REPO   = "ArielFrid24/technion-tracker"
VERSION_FILE  = os.path.join(OUTPUT_DIR, ".update_version")

app = Flask(__name__)
CORS(app)

# Courses that are typically exempted — won't be recommended by default
# Users can still add them manually if needed
COMMON_EXEMPTIONS = {
    "03240033",  # אנגלית טכנית
    "03240053",  # עברית
    "01030015",  # מתמטיקה מקדמית
    "01130013",  # פיסיקה מקדמית 1
    "01130014",  # פיסיקה מקדמית 2
}

def load_courses():
    courses = {}
    if not os.path.exists(LABELED_CSV):
        return courses
    with open(LABELED_CSV, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            try:    credits = float(row.get("credits") or 0)
            except: credits = 0
            try:    grade = float(row.get("avg_final_grade") or 0)
            except: grade = 0
            courses[row["course_id"]] = {
                "id":       row["course_id"],
                "name":     row["course_name"],
                "category": row["category"],
                "credits":  credits,
                "grade":    grade,
                "prereqs":  row.get("prereqs", "").strip(),
                "has_test": row.get("has_test", "").strip(),  # "1"/"0"/"" (not checked)
                "exam_date_a": row.get("exam_date_a", "").strip(),  # "DD-MM-YYYY" or ""
                "exam_date_b": row.get("exam_date_b", "").strip(),
            }
    return courses

COURSES_DB = load_courses()
print(f"Loaded {len(COURSES_DB)} courses")

def load_available(semester):
    path = os.path.join(OUTPUT_DIR, f"semester_{semester}.json")
    if not os.path.exists(path):
        jsons = sorted(glob.glob(os.path.join(OUTPUT_DIR, "semester_*.json")))
        if not jsons: return set()
        path = jsons[-1]
    with open(path, encoding="utf-8") as f:
        return set(json.load(f)["courses"])

def prereqs_met(course, taken_ids):
    prereq_str = course.get("prereqs", "").strip()
    if not prereq_str: return True
    tokens = prereq_str.split()
    or_groups, current = [], []
    for t in tokens:
        if t == "OR":   or_groups.append(current); current = []
        elif t != "AND": current.append(t)
    if current: or_groups.append(current)
    return any(all(cid in taken_ids for cid in group) for group in or_groups)

MANDATORY_SEMESTER = {
    "00940345":1,"01040031":1,"01040166":1,"02340117":1,"03240033":1,
    "00940700":2,"00940219":2,"00940412":2,"00940210":2,"01040032":2,"01140051":2,
    "00940224":3,"00940241":3,"00940424":3,"00950296":3,"00960570":3,
    "00940314":4,"00960211":4,"00960224":4,"00960327":4,"00960411":4,"00970414":4,
    "00960210":5,"00960250":5,"00960275":5,"00970209":5,"00970447":5,
    "00940290":7,"00940295":8,
}
CATEGORY_PRIORITY = {
    # NOTE: these must match the exact category strings stored in
    # courses_labeled.csv (c["category"]), NOT the REQ/progress bucket names
    # below — "קורסי בחירה בנתונים" (with the קורסי prefix) is what's actually
    # scraped, matching "קורסי בחירה פקולטיים"'s naming pattern.
    "עתיר נתונים":20,"קורסי בחירה בנתונים":21,"קורס מדעי":22,
    "מלג":23,"קורס ספורט":24,"בחירה חופשית":25,"קורסי בחירה פקולטיים":26,
}
def course_priority(c):
    return MANDATORY_SEMESTER.get(c["id"], CATEGORY_PRIORITY.get(c["category"], 99))

REQ = {
    # עתיר נתונים is a minimum COURSE COUNT (at least 2 data-intensive
    # courses), not a point total — matches how מלג_n/ספורט_n are counted.
    # חובה is ONLY courses actually tagged category="חובה" (102.5 pts across
    # the 29 mandatory courses) — kept fully separate from קורס מדעי (5.5
    # pts) even though the official program groups them together as part of
    # the same 108-pt requirement, so the app never reports "missing pts"
    # against a bucket a student can't actually see which courses fill.
    "חובה":102.5,"קורס מדעי":5.5,"בחירה בנתונים":24.5,"עתיר נתונים_n":2,
    "בחירה פקולטית":10.5,"ספורט_n":2,"מלג_n":3,"בחירה חופשית":6.0,"total":155.0,
}

def compute_progress(taken_ids):
    p = {k:0.0 for k in ["חובה","קורס מדעי","בחירה בנתונים",
                           "בחירה פקולטית","ספורט_n","מלג_n","עתיר נתונים_n","בחירה חופשית","total"]}
    sport_n = malag_n = atir_n = 0
    for cid in taken_ids:
        c = COURSES_DB.get(cid)
        if not c: continue
        cat, cr = c["category"], c["credits"]
        if cat == "חובה":                    p["חובה"] += cr
        elif cat == "קורס מדעי":             p["קורס מדעי"] += cr
        elif cat in ("קורסי בחירה בנתונים","עתיר נתונים"):
            p["בחירה בנתונים"] += cr
            if cat == "עתיר נתונים":         atir_n += 1
        elif cat == "קורסי בחירה פקולטיים":  p["בחירה פקולטית"] += cr
        elif cat == "קורס ספורט":
            sport_n += 1
            if sport_n > 2: p["בחירה חופשית"] += cr
        elif cat == "מלג":
            malag_n += 1
            if malag_n > REQ["מלג_n"]: p["בחירה חופשית"] += cr
        elif cat == "בחירה חופשית":          p["בחירה חופשית"] += cr
        p["total"] += cr
    p["ספורט_n"]       = sport_n
    p["מלג_n"]         = malag_n
    p["עתיר נתונים_n"] = atir_n

    # Points earned beyond a category's own requirement roll over into the
    # next broader elective bucket instead of being ignored — surplus data
    # electives count toward faculty electives, and surplus faculty
    # electives (plus surplus science, and surplus sport/מלג already folded
    # in above) count toward free choice.
    data_overflow = max(0.0, p["בחירה בנתונים"] - REQ["בחירה בנתונים"])
    p["בחירה בנתונים"] = min(p["בחירה בנתונים"], REQ["בחירה בנתונים"])
    p["בחירה פקולטית"] += data_overflow

    fac_overflow = max(0.0, p["בחירה פקולטית"] - REQ["בחירה פקולטית"])
    p["בחירה פקולטית"] = min(p["בחירה פקולטית"], REQ["בחירה פקולטית"])
    p["בחירה חופשית"] += fac_overflow

    sci_overflow = max(0.0, p["קורס מדעי"] - REQ["קורס מדעי"])
    p["קורס מדעי"] = min(p["קורס מדעי"], REQ["קורס מדעי"])
    p["בחירה חופשית"] += sci_overflow

    return p

def weighted_grade(courses):
    graded = [c for c in courses if c["grade"] > 0]
    total  = sum(c["credits"] for c in graded)
    if total == 0: return 0
    return sum(c["credits"] * c["grade"] for c in graded) / total

def _closest_subset(items, max_units):
    """
    0/1 subset-sum: given `items` (course dicts, already sorted by
    preference — priority then -grade) and a point budget `max_units`
    (credits * 2, so everything is an integer), find the subset whose
    credit-sum is the largest value <= max_units — i.e. fills the gap as
    exactly as possible instead of stopping at the first course that
    doesn't overshoot, which is what let a category's courses that all
    share one credit size (e.g. מלג at 2.0 each) get skipped entirely
    when the leftover gap didn't happen to match that size.

    Standard subset-sum DP with backpointers. Processing `items` in
    preference order and never overwriting an already-reached sum means
    equally-good sums prefer earlier (higher-priority/higher-grade)
    items — a cheap way to bias the result without a full optimal search.
    """
    if max_units <= 0 or not items:
        return [], 0
    reached = [None] * (max_units + 1)   # reached[s] = (item_index, prev_sum) or None
    reached[0] = (-1, -1)
    for idx, c in enumerate(items):
        w = round(c["credits"] * 2)
        if w <= 0 or w > max_units: continue
        for s in range(max_units, w - 1, -1):
            if reached[s] is None and reached[s - w] is not None:
                reached[s] = (idx, s - w)

    best_s = next((s for s in range(max_units, -1, -1) if reached[s] is not None), 0)
    chosen, s = [], best_s
    while s > 0:
        idx, prev_s = reached[s]
        chosen.append(items[idx])
        s = prev_s
    return chosen, best_s

def recommend(available_ids, taken_ids, target_pts, must_ids=None, block_ids=None, failed_ids=None, exam_pref=None):
    must_ids = set(must_ids or [])
    exam_pref = exam_pref or {}  # { category_string: "with" | "without" | "any" }
    failed_ids_set = set(failed_ids or [])
    # For prereq checking, only truly passed courses count
    assumed  = (taken_ids - failed_ids_set) | must_ids
    progress = compute_progress(taken_ids)

    cat_remaining = {
        "עתיר נתונים":          max(0, REQ["עתיר נתונים_n"] - progress["עתיר נתונים_n"]),
        "קורסי בחירה בנתונים": max(0, REQ["בחירה בנתונים"] - progress["בחירה בנתונים"]),
        "קורס מדעי":            max(0, REQ["קורס מדעי"]      - progress["קורס מדעי"]),
        "מלג":                  max(0, REQ["מלג_n"]          - progress["מלג_n"]),
        "קורס ספורט":           max(0, REQ["ספורט_n"]        - progress["ספורט_n"]),
        "בחירה חופשית":         max(0, REQ["בחירה חופשית"]   - progress["בחירה חופשית"]),
        "קורסי בחירה פקולטיים": max(0, REQ["בחירה פקולטית"] - progress["בחירה פקולטית"]),
    }
    cat_added = {k: 0.0 for k in cat_remaining}

    block_ids  = set(block_ids or [])
    failed_ids = set(failed_ids or [])
    # passed = taken but not failed; failed courses are eligible for retake
    passed_ids = taken_ids - failed_ids
    candidates = []
    for cid in available_ids:
        if cid in passed_ids: continue   # already passed — skip
        if cid in block_ids: continue    # user blocked it
        if cid in COMMON_EXEMPTIONS and cid not in must_ids: continue  # typically exempted
        c = COURSES_DB.get(cid)
        if not c or c["credits"] == 0: continue
        pref = exam_pref.get(c["category"])
        # only filter when we actually have exam data for this course (has_test
        # is "1"/"0"); courses we never checked ("") pass through regardless
        if pref == "with"    and c["has_test"] != "1": continue
        if pref == "without" and c["has_test"] != "0": continue
        if not prereqs_met(c, assumed): continue
        candidates.append(c)

    must_courses = [COURSES_DB[cid] for cid in must_ids if cid in COURSES_DB]
    must_pts     = sum(c["credits"] for c in must_courses)
    free_cands   = [c for c in candidates if c["id"] not in must_ids]
    free_cands.sort(key=lambda c: (course_priority(c), -c["grade"]))

    # Drop courses whose מועד א' exam date collides with a required course's,
    # and collapse the rest to at most one course per exam date — the fill
    # passes below add courses one at a time / via subset-sum and have no
    # other way to express "these two are mutually exclusive," so pruning the
    # candidate pool up front is what actually keeps two exam-day clashes
    # from both landing in the same schedule. Courses with no exam or
    # unchecked exam data (exam_date_a == "") are never restricted.
    locked_dates = {c["exam_date_a"] for c in must_courses if c["exam_date_a"]}
    seen_dates = set(locked_dates)
    deduped = []
    for c in free_cands:
        d = c["exam_date_a"]
        if d and d in seen_dates: continue
        if d: seen_dates.add(d)
        deduped.append(c)
    free_cands = deduped

    def quota_ok(c):
        cat = c["category"]
        if cat == "עתיר נתונים":
            # עתיר נתונים is a subset of קורסי בחירה בנתונים — allow it while
            # either its own course-count minimum OR the shared elective
            # point cap still has room, so it isn't blocked once the count is
            # met while elective points remain, nor allowed to blow past the
            # elective cap once the count is already satisfied.
            return (cat_added["עתיר נתונים"] < cat_remaining["עתיר נתונים"]
                    or cat_added["קורסי בחירה בנתונים"] < cat_remaining["קורסי בחירה בנתונים"])
        if cat not in cat_remaining: return True
        return cat_added[cat] < cat_remaining[cat]

    def add_cat(c):
        cat = c["category"]
        if cat == "עתיר נתונים":
            cat_added["עתיר נתונים"]          += 1
            cat_added["קורסי בחירה בנתונים"]  += c["credits"]  # also debits the shared elective cap
            return
        if cat not in cat_added: return
        cat_added[cat] += 1 if cat in ("מלג","קורס ספורט") else c["credits"]

    schedule = list(must_courses)
    pts = must_pts
    for c in must_courses:
        add_cat(c)

    # Pass 1: quota-aware fill
    for c in free_cands:
        if pts + c["credits"] > target_pts + 0.01: continue
        if not quota_ok(c): continue
        schedule.append(c); pts += c["credits"]; add_cat(c)
        if pts >= target_pts - 0.01: break

    # Pass 2: if still short, close the remaining gap with the best-fitting
    # *combination* of whatever's left (ignoring quotas), instead of a
    # single first-fit course — see _closest_subset for why this matters.
    if pts < target_pts - 0.24:
        remaining = [c for c in free_cands if c not in schedule]
        gap_units = round((target_pts - pts) * 2)
        chosen, filled_units = _closest_subset(remaining, gap_units)
        schedule.extend(chosen)
        pts += filled_units / 2

    # Pass 3: pass 1's own greedy picks can themselves be the problem — a
    # locally-fine choice can lock in a leftover gap nothing combines to
    # close, even though a *different* set of courses hits the target
    # exactly. If we're still short, throw away pass 1/2's picks and run
    # one clean subset-sum over the whole free pool; keep it only if it
    # gets closer to target than what we already had.
    if pts < target_pts - 0.24:
        full_units = round((target_pts - must_pts) * 2)
        chosen2, filled2_units = _closest_subset(free_cands, full_units)
        if filled2_units / 2 > pts - must_pts:
            schedule = list(must_courses) + chosen2
            pts = must_pts + filled2_units / 2

    if pts < target_pts - 0.24: return None, 0
    return schedule, weighted_grade(schedule)

def fmt_schedule(schedule, must_ids):
    return {
        "weighted_grade": round(weighted_grade(schedule), 1),
        "total_credits":  round(sum(c["credits"] for c in schedule), 1),
        "n_courses":      len(schedule),
        "courses": [{
            "id":       c["id"],
            "name":     c["name"],
            "category": c["category"],
            "credits":  c["credits"],
            "grade":    round(c["grade"], 1),
            "must":     c["id"] in set(must_ids),
            "has_test": c["has_test"],  # "1"/"0"/""
            "exam_date_a": c["exam_date_a"],
        } for c in sorted(schedule, key=lambda x: (course_priority(x), -x["grade"]))]
    }

@app.route("/api/semesters")
def api_semesters():
    """Return all semester JSONs found on disk, sorted newest first."""
    jsons = sorted(glob.glob(os.path.join(OUTPUT_DIR, "semester_*.json")), reverse=True)
    result = []
    for path in jsons:
        code = os.path.basename(path).replace("semester_","").replace(".json","")
        if not re.match(r"^\d{6}$", code): continue
        y, t = code[:4], code[4:]
        if t == "01":   label = f"Winter {y[2:]}"
        elif t == "02": label = f"Spring {str(int(y)+1)[2:]}"
        elif t == "03": label = f"Summer {str(int(y)+1)[2:]}"
        else:           label = code
        result.append({"code": code, "label": label})
    return jsonify({"semesters": result})

@app.route("/api/available")
def api_available():
    semester = request.args.get("semester", "")
    if not re.match(r"^\d{6}$", semester):
        return jsonify({"error": "invalid semester"}), 400
    available = load_available(semester)
    count = len(available & set(COURSES_DB.keys()))
    return jsonify({"semester": semester, "count": count})

@app.route("/api/semester-courses")
def api_semester_courses():
    """Return the actual course-ID list offered in a semester (not just a count),
    for the course-browsing table."""
    semester = request.args.get("semester", "")
    if not re.match(r"^\d{6}$", semester):
        return jsonify({"error": "invalid semester"}), 400
    available = load_available(semester)
    courses = sorted(available & set(COURSES_DB.keys()))
    return jsonify({"semester": semester, "courses": courses})

@app.route("/api/recommend", methods=["POST"])
def api_recommend():
    body      = request.json or {}
    semester  = body.get("semester", "")
    taken_ids  = set(body.get("taken", []))
    failed_ids = set(body.get("failed", []))  # failed courses — still eligible to retake
    must_ids   = list(body.get("must", []))
    block_ids  = set(body.get("block", []))
    min_pts   = float(body.get("min", 9))
    max_pts   = float(body.get("max", 12))
    exam_pref = body.get("examPref", {}) or {}

    if not re.match(r"^\d{6}$", semester):
        return jsonify({"error": "invalid semester"}), 400

    print(f"[recommend] taken={len(taken_ids)} failed={len(failed_ids)} must={must_ids} block={len(block_ids)}")
    print(f"[recommend] failed courses: {failed_ids}")

    available = load_available(semester)
    options, seen = [], set()
    pt = min_pts
    while pt <= max_pts + 0.01:
        schedule, score = recommend(available, taken_ids, pt, must_ids, block_ids, failed_ids, exam_pref)
        if schedule:
            key = frozenset(c["id"] for c in schedule)
            if key not in seen:
                seen.add(key)
                options.append(fmt_schedule(schedule, must_ids))
        pt = round(pt + 0.5, 1)

    if not options:
        return jsonify({"error": "No valid schedule found for the given range"}), 404
    return jsonify({"options": options})

@app.route("/api/status", methods=["POST"])
def api_status():
    taken_ids = set(request.json.get("taken", []))
    must_ids  = set(request.json.get("must", []))
    prog      = compute_progress(taken_ids)
    # "missing" reflects taken + planned must-take courses, so a category
    # covered by a course you've just marked as required shows as done
    # instead of still nagging you about points you're already about to earn
    prog_planned = compute_progress(taken_ids | must_ids) if must_ids else prog
    missing   = {
        "חובה":           round(max(0, REQ["חובה"]          - prog_planned["חובה"]), 1),
        "קורס מדעי":      round(max(0, REQ["קורס מדעי"]     - prog_planned["קורס מדעי"]), 1),
        "בחירה בנתונים":  round(max(0, REQ["בחירה בנתונים"] - prog_planned["בחירה בנתונים"]), 1),
        "עתיר נתונים_n":  max(0, REQ["עתיר נתונים_n"] - prog_planned["עתיר נתונים_n"]),
        "בחירה פקולטית":  round(max(0, REQ["בחירה פקולטית"] - prog_planned["בחירה פקולטית"]), 1),
        "ספורט_n":        max(0, REQ["ספורט_n"]             - prog_planned["ספורט_n"]),
        "מלג_n":          max(0, REQ["מלג_n"]               - prog_planned["מלג_n"]),
        "בחירה חופשית":   round(max(0, REQ["בחירה חופשית"]  - prog_planned["בחירה חופשית"]), 1),
        "total":          round(max(0, REQ["total"]          - prog_planned["total"]), 1),
    }
    return jsonify({"progress": prog, "missing": missing, "requirements": REQ})

@app.route("/api/reload")
def api_reload():
    global COURSES_DB
    COURSES_DB = load_courses()
    return jsonify({"count": len(COURSES_DB)})

def _read_local_version():
    # A real git checkout (the developer's own clone) always knows exactly
    # what commit it's on — use that instead of .update_version so it isn't
    # permanently flagged as "outdated" just because that marker file (only
    # ever written by the zip-based updater below) doesn't exist yet.
    if os.path.isdir(os.path.join(OUTPUT_DIR, ".git")):
        try:
            head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=OUTPUT_DIR,
                                   capture_output=True, text=True, timeout=5)
            if head.returncode == 0:
                return head.stdout.strip()
        except Exception:
            pass
    if os.path.exists(VERSION_FILE):
        return open(VERSION_FILE, encoding="utf-8").read().strip()
    return None

def _fetch_latest_commit():
    req = urllib.request.Request(
        f"https://api.github.com/repos/{GITHUB_REPO}/commits/main",
        headers={"User-Agent": "technion-tracker-update", "Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())
    return {
        "sha":     data["sha"],
        "message": data["commit"]["message"].split("\n")[0],
        "date":    data["commit"]["author"]["date"],
    }

@app.route("/api/update/check")
def api_update_check():
    try:
        latest = _fetch_latest_commit()
    except Exception as e:
        return jsonify({"error": f"Could not check for updates: {e}"}), 502
    current = _read_local_version()
    return jsonify({
        "current":          current,
        "latest":           latest["sha"],
        "latest_message":   latest["message"],
        "latest_date":      latest["date"],
        "update_available": current != latest["sha"],
    })

@app.route("/api/update/apply", methods=["POST"])
def api_update_apply():
    try:
        latest = _fetch_latest_commit()

        zip_req = urllib.request.Request(
            f"https://github.com/{GITHUB_REPO}/archive/refs/heads/main.zip",
            headers={"User-Agent": "technion-tracker-update"},
        )
        with urllib.request.urlopen(zip_req, timeout=120) as resp:
            zip_bytes = resp.read()

        with tempfile.TemporaryDirectory() as tmp:
            zipfile.ZipFile(io.BytesIO(zip_bytes)).extractall(tmp)
            # GitHub zips the branch into a single "<repo>-<branch>" folder
            extracted_root = os.path.join(tmp, os.listdir(tmp)[0])

            def copy_tree(src, dst):
                for name in os.listdir(src):
                    s, d = os.path.join(src, name), os.path.join(dst, name)
                    if os.path.isdir(s):
                        os.makedirs(d, exist_ok=True)
                        copy_tree(s, d)
                    else:
                        shutil.copy2(s, d)

            copy_tree(extracted_root, OUTPUT_DIR)

        with open(VERSION_FILE, "w", encoding="utf-8") as f:
            f.write(latest["sha"])

        global COURSES_DB
        COURSES_DB = load_courses()
        return jsonify({"ok": True, "version": latest["sha"]})
    except Exception as e:
        return jsonify({"error": f"Update failed: {e}"}), 500

if __name__ == "__main__":
    # use_reloader=False matters here: /api/update/apply overwrites app.py
    # itself (and other .py files) mid-request. With the reloader on, it
    # detects that file change and kills+restarts the worker process before
    # the response can be sent — the browser sees that as "Failed to fetch".
    app.run(port=5000, debug=True, use_reloader=False)