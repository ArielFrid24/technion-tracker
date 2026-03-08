import csv
import os

# ----------------------------
# Config
# ----------------------------
INPUT_CSV   = "courses_aggregated_filtered.csv"
OUTPUT_CSV  = "courses_ranked_worst_to_best.csv"
MIN_REVIEWS = 10

# ----------------------------
# Load
# ----------------------------
script_dir  = os.path.dirname(os.path.abspath(__file__))
input_path  = os.path.join(script_dir, INPUT_CSV)
output_path = os.path.join(script_dir, OUTPUT_CSV)

with open(input_path, encoding="utf-8-sig") as f:
    rows = list(csv.DictReader(f))

def safe_float(v):
    try: return float(v) if v not in (None, "") else None
    except: return None

def safe_int(v):
    try: return int(v) if v not in (None, "") else 0
    except: return 0

usable = [r for r in rows
          if safe_float(r["avg_general_rank"]) is not None
          and safe_int(r["n_general_rank"]) >= MIN_REVIEWS]

ranked = sorted(usable, key=lambda r: safe_float(r["avg_general_rank"]))

# Add a rank column
for i, r in enumerate(ranked, 1):
    r["rank"] = i

fieldnames = ["rank"] + [k for k in rows[0].keys() if k != "rank"]
with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=fieldnames)
    w.writeheader()
    w.writerows(ranked)

print(f"Saved {len(ranked)} courses (worst to best) -> {output_path}")