"""
add_sport.py
Finds all 0394xxxx courses from courses_aggregated_all.csv,
labels them as קורס ספורט with credits=1, no prereqs.
"""
import csv, os

OUTPUT_DIR  = "C:/Users/pc/OneDrive - Technion/Desktop/cheesefork_scraper"
LABELED_CSV = os.path.join(OUTPUT_DIR, "courses_labeled.csv")
AGG_CSV     = os.path.join(OUTPUT_DIR, "courses_aggregated_all.csv")

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

def main():
    existing_rows, cols = load_labeled()
    agg = load_agg()

    if "semester" not in cols:
        cols = cols + ["semester"]

    sport_courses = sorted(cid for cid in agg if cid.startswith("0394"))
    print(f"Found {len(sport_courses)} sport courses (0394xxxx)")

    existing_rows = [r for r in existing_rows if r.get("category") != "קורס ספורט"]

    new_rows = []
    for cid in sport_courses:
        ex = agg[cid]
        new_rows.append({
            "course_id":        cid,
            "course_name":      ex.get("course_name", ""),
            "category":         "קורס ספורט",
            "credits":          1,
            "prereqs":          "",
            "avg_final_grade":  ex.get("avg_final_grade", ""),
            "avg_general_rank": ex.get("avg_general_rank", ""),
            "n_general_rank":   ex.get("n_general_rank", ""),
            "semester":         "",
        })
        print(f"  {cid} - {ex.get('course_name', '')}")

    all_rows = existing_rows + new_rows

    with open(LABELED_CSV, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(all_rows)

    print(f"\nSaved {len(all_rows)} total rows -> {LABELED_CSV}")
    print(f"  קורס ספורט: {len(new_rows)}")

main()