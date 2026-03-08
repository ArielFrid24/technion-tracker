import csv
import os

# ----------------------------
# Config
# ----------------------------
INPUT_CSV  = "courses_aggregated_all.csv"   # put this in the same folder as the script
OUTPUT_CSV = "courses_aggregated_filtered.csv"
MIN_REVIEWS = 10  # minimum n_general_rank to keep a course

# ----------------------------
# Correlation helpers
# ----------------------------
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

def fmt(v, d=4):
    return f"{v:.{d}f}" if v is not None else "N/A"

# ----------------------------
# Load CSV
# ----------------------------
script_dir = os.path.dirname(os.path.abspath(__file__))
input_path  = os.path.join(script_dir, INPUT_CSV)
output_path = os.path.join(script_dir, OUTPUT_CSV)

with open(input_path, encoding="utf-8-sig") as f:
    rows = list(csv.DictReader(f))

print(f"Loaded {len(rows)} courses from {INPUT_CSV}")

# ----------------------------
# Filter
# ----------------------------
def safe_float(v):
    try: return float(v) if v not in (None, "") else None
    except: return None

def safe_int(v):
    try: return int(v) if v not in (None, "") else 0
    except: return 0

total        = len(rows)
has_both     = [r for r in rows if safe_float(r["avg_final_grade"]) is not None
                                and safe_float(r["avg_general_rank"]) is not None]
filtered     = [r for r in has_both if safe_int(r["n_general_rank"]) >= MIN_REVIEWS]

print(f"  Has both grade + rating:          {len(has_both)}")
print(f"  After filtering < {MIN_REVIEWS} reviews:    {len(filtered)}")
print(f"  Dropped:                          {len(has_both) - len(filtered)}")

# ----------------------------
# Correlation — before and after filter
# ----------------------------
xs_all = [safe_float(r["avg_final_grade"])  for r in has_both]
ys_all = [safe_float(r["avg_general_rank"]) for r in has_both]

xs_fil = [safe_float(r["avg_final_grade"])  for r in filtered]
ys_fil = [safe_float(r["avg_general_rank"]) for r in filtered]

print(f"\nCorrelation BEFORE filter ({len(has_both)} courses):")
print(f"  Pearson  = {fmt(pearson(xs_all, ys_all))}")
print(f"  Spearman = {fmt(spearman(xs_all, ys_all))}")

print(f"\nCorrelation AFTER filter  ({len(filtered)} courses, >= {MIN_REVIEWS} reviews):")
print(f"  Pearson  = {fmt(pearson(xs_fil, ys_fil))}")
print(f"  Spearman = {fmt(spearman(xs_fil, ys_fil))}")

# ----------------------------
# Save filtered CSV
# ----------------------------
fieldnames = list(rows[0].keys())
with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=fieldnames)
    w.writeheader()
    w.writerows(filtered)

print(f"\nSaved: {output_path}  ({len(filtered)} rows)")

# ----------------------------
# Quick summary
# ----------------------------
ranked = sorted(filtered, key=lambda r: safe_float(r["avg_general_rank"]))
print(f"\nWorst rated: {ranked[0]['course_id']} {ranked[0]['course_name']}"
      f"  (rank={safe_float(ranked[0]['avg_general_rank']):.2f}, "
      f"grade={safe_float(ranked[0]['avg_final_grade']):.1f}, "
      f"n={ranked[0]['n_general_rank']} reviews)")
print(f"Best rated:  {ranked[-1]['course_id']} {ranked[-1]['course_name']}"
      f"  (rank={safe_float(ranked[-1]['avg_general_rank']):.2f}, "
      f"grade={safe_float(ranked[-1]['avg_final_grade']):.1f}, "
      f"n={ranked[-1]['n_general_rank']} reviews)")