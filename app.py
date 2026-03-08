"""
app.py — Technion course recommender backend
"""
import csv, glob, json, os, re
from flask import Flask, jsonify, request
from flask_cors import CORS

OUTPUT_DIR  = os.path.dirname(os.path.abspath(__file__))
LABELED_CSV = os.path.join(OUTPUT_DIR, "courses_labeled.csv")

app = Flask(__name__)
CORS(app)

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
    "00940700":2,"00940412":2,"00940210":2,"01040032":2,"01140051":2,
    "00940224":3,"00940241":3,"00940424":3,"00950296":3,"00960570":3,
    "00940314":4,"00960211":4,"00960224":4,"00960327":4,"00960411":4,"00970414":4,
    "00960210":5,"00960250":5,"00960275":5,"00970209":5,"00970447":5,
    "00940290":7,"00940295":8,
}
CATEGORY_PRIORITY = {
    "עתיר נתונים":20,"בחירה בנתונים":21,"קורס מדעי":22,
    "מלג":23,"קורס ספורט":24,"בחירה חופשית":25,"קורסי בחירה פקולטיים":26,
}
def course_priority(c):
    return MANDATORY_SEMESTER.get(c["id"], CATEGORY_PRIORITY.get(c["category"], 99))

REQ = {
    "חובה":108.0,"קורס מדעי":5.5,"בחירה בנתונים":24.5,"עתיר נתונים":2.0,
    "בחירה פקולטית":10.5,"ספורט_n":2,"מלג_n":2,"בחירה חופשית":6.0,"total":155.0,
}

def compute_progress(taken_ids):
    p = {k:0.0 for k in ["חובה","קורס מדעי","בחירה בנתונים","עתיר נתונים",
                           "בחירה פקולטית","ספורט_n","מלג_n","בחירה חופשית","total"]}
    sport_n = malag_n = 0
    for cid in taken_ids:
        c = COURSES_DB.get(cid)
        if not c: continue
        cat, cr = c["category"], c["credits"]
        if cat == "חובה":                    p["חובה"] += cr
        elif cat == "קורס מדעי":             p["קורס מדעי"] += cr; p["חובה"] += cr
        elif cat in ("בחירה בנתונים","עתיר נתונים"):
            p["בחירה בנתונים"] += cr
            if cat == "עתיר נתונים":         p["עתיר נתונים"] += cr
        elif cat == "קורסי בחירה פקולטיים":  p["בחירה פקולטית"] += cr
        elif cat == "קורס ספורט":
            sport_n += 1
            if sport_n > 2: p["בחירה חופשית"] += cr
        elif cat == "מלג":
            malag_n += 1
            if malag_n > 2: p["בחירה חופשית"] += cr
        elif cat == "בחירה חופשית":          p["בחירה חופשית"] += cr
        p["total"] += cr
    p["ספורט_n"] = sport_n
    p["מלג_n"]   = malag_n
    return p

def weighted_grade(courses):
    graded = [c for c in courses if c["grade"] > 0]
    total  = sum(c["credits"] for c in graded)
    if total == 0: return 0
    return sum(c["credits"] * c["grade"] for c in graded) / total

def recommend(available_ids, taken_ids, target_pts, must_ids=None, block_ids=None, failed_ids=None):
    must_ids = set(must_ids or [])
    failed_ids_set = set(failed_ids or [])
    # For prereq checking, only truly passed courses count
    assumed  = (taken_ids - failed_ids_set) | must_ids
    progress = compute_progress(taken_ids)

    cat_remaining = {
        "עתיר נתונים":          max(0, REQ["עתיר נתונים"]   - progress["עתיר נתונים"]),
        "בחירה בנתונים":        max(0, REQ["בחירה בנתונים"] - progress["בחירה בנתונים"]),
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
        c = COURSES_DB.get(cid)
        if not c or c["credits"] == 0: continue
        if not prereqs_met(c, assumed): continue
        candidates.append(c)

    must_courses = [COURSES_DB[cid] for cid in must_ids if cid in COURSES_DB]
    must_pts     = sum(c["credits"] for c in must_courses)
    free_cands   = [c for c in candidates if c["id"] not in must_ids]
    free_cands.sort(key=lambda c: (course_priority(c), -c["grade"]))

    def quota_ok(c):
        cat = c["category"]
        if cat not in cat_remaining: return True
        return cat_added[cat] < cat_remaining[cat]

    def add_cat(c):
        cat = c["category"]
        if cat not in cat_added: return
        cat_added[cat] += 1 if cat in ("מלג","קורס ספורט") else c["credits"]

    schedule = list(must_courses)
    pts = must_pts

    # Pass 1: quota-aware fill
    for c in free_cands:
        if pts + c["credits"] > target_pts + 0.01: continue
        if not quota_ok(c): continue
        schedule.append(c); pts += c["credits"]; add_cat(c)
        if pts >= target_pts - 0.01: break

    # Pass 2: fill remaining gap ignoring quotas
    if pts < target_pts - 0.24:
        for c in free_cands:
            if c in schedule: continue
            if pts + c["credits"] > target_pts + 0.01: continue
            schedule.append(c); pts += c["credits"]
            if pts >= target_pts - 0.24: break

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
        } for c in sorted(schedule, key=lambda x: (course_priority(x), -x["grade"]))]
    }

@app.route("/api/available")
def api_available():
    semester = request.args.get("semester", "")
    if not re.match(r"^\d{6}$", semester):
        return jsonify({"error": "invalid semester"}), 400
    available = load_available(semester)
    count = len(available & set(COURSES_DB.keys()))
    return jsonify({"semester": semester, "count": count})

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

    if not re.match(r"^\d{6}$", semester):
        return jsonify({"error": "invalid semester"}), 400

    print(f"[recommend] taken={len(taken_ids)} failed={len(failed_ids)} must={must_ids} block={len(block_ids)}")
    print(f"[recommend] failed courses: {failed_ids}")

    available = load_available(semester)
    options, seen = [], set()
    pt = min_pts
    while pt <= max_pts + 0.01:
        schedule, score = recommend(available, taken_ids, pt, must_ids, block_ids, failed_ids)
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
    prog      = compute_progress(taken_ids)
    missing   = {
        "חובה":           round(max(0, REQ["חובה"]          - prog["חובה"]), 1),
        "קורס מדעי":      round(max(0, REQ["קורס מדעי"]     - prog["קורס מדעי"]), 1),
        "בחירה בנתונים":  round(max(0, REQ["בחירה בנתונים"] - prog["בחירה בנתונים"]), 1),
        "עתיר נתונים":    round(max(0, REQ["עתיר נתונים"]   - prog["עתיר נתונים"]), 1),
        "בחירה פקולטית":  round(max(0, REQ["בחירה פקולטית"] - prog["בחירה פקולטית"]), 1),
        "ספורט_n":        max(0, REQ["ספורט_n"]             - prog["ספורט_n"]),
        "מלג_n":          max(0, REQ["מלג_n"]               - prog["מלג_n"]),
        "בחירה חופשית":   round(max(0, REQ["בחירה חופשית"]  - prog["בחירה חופשית"]), 1),
        "total":          round(max(0, REQ["total"]          - prog["total"]), 1),
    }
    return jsonify({"progress": prog, "missing": missing, "requirements": REQ})

@app.route("/api/reload")
def api_reload():
    global COURSES_DB
    COURSES_DB = load_courses()
    return jsonify({"count": len(COURSES_DB)})

if __name__ == "__main__":
    app.run(port=5000, debug=True)