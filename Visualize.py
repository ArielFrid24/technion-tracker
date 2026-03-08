"""
visualize.py  —  Technion course data visualizations
"""
import csv, os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
from collections import defaultdict

# ── Config ─────────────────────────────────────────────────────────────────────
INPUT_CSV   = "courses_aggregated_all.csv"
OUTPUT_DIR  = "C:/Users/pc/OneDrive - Technion/Desktop/cheesefork_scraper/plots"
MIN_REVIEWS = 10
TOP_N       = 15

plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "figure.dpi":  150,
    "axes.spines.top":   False,
    "axes.spines.right": False,
})

try:
    from bidi.algorithm import get_display
    def heb(s, maxlen=28):
        if len(s) > maxlen: s = s[:maxlen] + "…"
        return get_display(s)
except ImportError:
    def heb(s, maxlen=28):
        if len(s) > maxlen: s = s[:maxlen] + "…"
        return " ".join(reversed(s.split()))

# ── Load & filter ──────────────────────────────────────────────────────────────
script_dir = os.path.dirname(os.path.abspath(__file__))
os.makedirs(OUTPUT_DIR, exist_ok=True)

with open(os.path.join(script_dir, INPUT_CSV), encoding="utf-8-sig") as f:
    all_rows = list(csv.DictReader(f))

def sf(v):
    try: return float(v) if v not in (None,"") else None
    except: return None
def si(v):
    try: return int(v)   if v not in (None,"") else 0
    except: return 0

rows = [r for r in all_rows
        if sf(r["avg_final_grade"])  is not None
        and sf(r["avg_general_rank"]) is not None
        and si(r["n_general_rank"])  >= MIN_REVIEWS]

grades  = [sf(r["avg_final_grade"])  for r in rows]
ratings = [sf(r["avg_general_rank"]) for r in rows]
names   = [r["course_name"]          for r in rows]
ids     = [r["course_id"]            for r in rows]
print(f"Loaded {len(rows)} courses (>= {MIN_REVIEWS} reviews)")

def pearson(xs, ys):
    if len(xs) < 2: return 0
    mx, my = sum(xs)/len(xs), sum(ys)/len(ys)
    num = sum((x-mx)*(y-my) for x,y in zip(xs,ys))
    den = (sum((x-mx)**2 for x in xs)*sum((y-my)**2 for y in ys))**.5
    return num/den if den else 0

r_val = pearson(grades, ratings)

# Map based on 3-digit course prefix
FACULTY_MAP = {
    "001": "Civil & Env Eng",
    "003": "Mechanical Eng",
    "004": "Electrical & CS",
    "005": "Chemical Eng",
    "006": "General / Projects",
    "008": "Aerospace Eng",
    "009": "Industrial Eng & Mgmt",
    "010": "Mathematics",
    "011": "Physics",
    "012": "Chemistry",
    "013": "Biology",
    "019": "Applied Mathematics",
    "020": "Architecture",
    "021": "Education in Sci & Tech",
    "023": "Computer Science",
    "027": "Medicine",
    "031": "Materials Eng",
    "032": "Humanities & Arts",
    "033": "Biomedical Eng",
    "039": "Music",
    "073": "Autonomous Systems",
    "074": "Management",
    "085": "Polymer Eng",
    "970": "Preparatory",
}
def faculty(cid): return FACULTY_MAP.get(cid[:3], f"Other ({cid[:3]})")
faculties = [faculty(r["course_id"]) for r in rows]

fac_data = defaultdict(lambda: {"grades":[],"ratings":[],"names":[]})
for r, g, f, n in zip(ratings, grades, faculties, names):
    fac_data[f]["grades"].append(g)
    fac_data[f]["ratings"].append(r)
    fac_data[f]["names"].append(n)

# ── PLOT 1: Scatter ────────────────────────────────────────────────────────────
fig, ax = plt.subplots(figsize=(10,7))
fac_list  = sorted(set(faculties))
cmap      = plt.get_cmap("tab20", len(fac_list))
fac_color = {f: cmap(i) for i,f in enumerate(fac_list)}
colors    = [fac_color[f] for f in faculties]

ax.scatter(grades, ratings, c=colors, alpha=0.7, s=60, edgecolors="white", linewidths=0.4)
m, b = np.polyfit(grades, ratings, 1)
xs = np.linspace(min(grades), max(grades), 100)
ax.plot(xs, m*xs+b, color="black", linewidth=1.5, linestyle="--", alpha=0.6)

combined = sorted(zip(ratings, grades, names))
for rt, gr, nm in combined[:3] + combined[-3:]:
    ax.annotate(heb(nm, 20), (gr, rt), fontsize=6.5, ha="left",
                xytext=(4,2), textcoords="offset points", color="#333333")

ax.set_xlabel("Average Final Grade", fontsize=12)
ax.set_ylabel("Average Student Rating (1–5)", fontsize=12)
ax.set_title(f"Grade vs. Student Rating  (n={len(rows)}, Pearson r={r_val:.3f})", fontsize=14)
ax.set_ylim(0.5, 5.5)
legend_patches = [mpatches.Patch(color=fac_color[f], label=f) for f in fac_list]
ax.legend(handles=legend_patches, fontsize=7, loc="lower right",
          ncol=2, framealpha=0.8, title="Faculty", title_fontsize=8)
plt.tight_layout()
plt.savefig(os.path.join(OUTPUT_DIR,"1_scatter_grade_vs_rating.png")); plt.close()
print("Saved: 1_scatter_grade_vs_rating.png")

# ── PLOT 2: Distributions ──────────────────────────────────────────────────────
fig, (ax1, ax2) = plt.subplots(1,2, figsize=(12,5))
ax1.hist(grades, bins=30, color="#4C72B0", edgecolor="white", alpha=0.85)
ax1.axvline(np.mean(grades), color="red", linestyle="--", linewidth=1.5,
            label=f"Mean={np.mean(grades):.1f}")
ax1.set_xlabel("Average Final Grade", fontsize=11)
ax1.set_ylabel("Number of Courses", fontsize=11)
ax1.set_title("Distribution of Final Grades", fontsize=13)
ax1.legend()

ax2.hist(ratings, bins=20, color="#DD8452", edgecolor="white", alpha=0.85)
ax2.axvline(np.mean(ratings), color="red", linestyle="--", linewidth=1.5,
            label=f"Mean={np.mean(ratings):.2f}")
ax2.set_xlabel("Average Student Rating (1–5)", fontsize=11)
ax2.set_ylabel("Number of Courses", fontsize=11)
ax2.set_title("Distribution of Student Ratings", fontsize=13)
ax2.legend()
plt.suptitle(f"Grade & Rating Distributions  (n={len(rows)} courses)", fontsize=13)
plt.tight_layout()
plt.savefig(os.path.join(OUTPUT_DIR,"2_distributions.png")); plt.close()
print("Saved: 2_distributions.png")

# ── PLOT 3: Top worst & best courses ──────────────────────────────────────────
ranked = sorted(zip(ratings, grades, names, ids))
worst = ranked[:TOP_N]
best  = ranked[-TOP_N:][::-1]

fig, (ax1, ax2) = plt.subplots(1,2, figsize=(20,8))

def bar_chart(ax, data, color, title):
    labels = [heb(n) for _,_,n,_ in data]
    vals   = [rt     for rt,_,_,_ in data]
    gvals  = [gr     for _,gr,_,_ in data]
    ax.barh(range(len(data)), vals, color=color, alpha=0.85, edgecolor="white")
    for i,(v,g) in enumerate(zip(vals,gvals)):
        ax.text(v+0.04, i, f"{v:.2f}  (grade {g:.0f})", va="center", fontsize=8.5)
    ax.set_yticks(range(len(data)))
    ax.set_yticklabels(labels, fontsize=9)
    ax.tick_params(axis="y", length=0)
    ax.set_xlabel("Average Rating", fontsize=11)
    ax.set_title(title, fontsize=13, color=color)
    ax.set_xlim(0, 6.8)
    ax.invert_yaxis()

bar_chart(ax1, worst, "#d62728", f"Top {TOP_N} Worst Rated Courses")
bar_chart(ax2, best,  "#2ca02c", f"Top {TOP_N} Best Rated Courses")
plt.suptitle(f"Best & Worst Rated Courses  (≥{MIN_REVIEWS} reviews)", fontsize=14)
fig.subplots_adjust(left=0.30, right=0.97, wspace=0.50)
plt.savefig(os.path.join(OUTPUT_DIR,"3_top_worst_best.png")); plt.close()
print("Saved: 3_top_worst_best.png")

# ── PLOT 4: Correlation by faculty ────────────────────────────────────────────
fac_stats = sorted(
    [(f, pearson(d["grades"],d["ratings"]), len(d["grades"]),
      np.mean(d["grades"]), np.mean(d["ratings"]))
     for f,d in fac_data.items() if len(d["grades"]) >= 5],
    key=lambda x: x[1]
)

fig, ax = plt.subplots(figsize=(10,6))
fnames  = [f"{f}  (n={n})" for f,_,n,_,_ in fac_stats]
fvals   = [r for _,r,_,_,_ in fac_stats]
fcolors = ["#d62728" if v < 0 else "#2ca02c" for v in fvals]
ax.barh(range(len(fac_stats)), fvals, color=fcolors, alpha=0.8, edgecolor="white")
for i,(v,(_,_,_,mg,mr)) in enumerate(zip(fvals,fac_stats)):
    offset = 0.02 if v >= 0 else -0.02
    align  = "left" if v >= 0 else "right"
    ax.text(v+offset, i, f"r={v:.2f}  (avg grade {mg:.0f}, avg rating {mr:.1f})",
            va="center", ha=align, fontsize=8)
ax.axvline(0, color="black", linewidth=1)
ax.set_yticks(range(len(fac_stats)))
ax.set_yticklabels(fnames, fontsize=9)
ax.set_xlabel("Pearson Correlation (grade vs rating)", fontsize=11)
ax.set_title("Grade–Rating Correlation by Faculty", fontsize=13)
ax.set_xlim(-1.5, 1.5)
plt.tight_layout()
plt.savefig(os.path.join(OUTPUT_DIR,"4_correlation_by_faculty.png")); plt.close()
print("Saved: 4_correlation_by_faculty.png")

# ── PLOT 5: All faculties by avg rating (worst to best) ───────────────────────
fac_summary = [
    (f, np.mean(d["ratings"]), np.mean(d["grades"]), len(d["grades"]))
    for f, d in fac_data.items() if len(d["grades"]) >= 5
]

# --- 5a: by rating ---
by_rating = sorted(fac_summary, key=lambda x: x[1])  # worst to best
fig, ax = plt.subplots(figsize=(10, max(5, len(by_rating)*0.45)))
vals   = [x[1] for x in by_rating]
labels = [f"{x[0]}  (n={x[3]})" for x in by_rating]
n      = len(vals)
colors = ["#d62728"]*3 + ["#f4a261"]*(n-6) + ["#2ca02c"]*3
ax.barh(range(n), vals, color=colors, alpha=0.85, edgecolor="white")
for i, (v, x) in enumerate(zip(vals, by_rating)):
    ax.text(v+0.03, i, f"{v:.2f}  (grade {x[2]:.0f})", va="center", fontsize=8.5)
ax.set_yticks(range(n))
ax.set_yticklabels(labels, fontsize=10)
ax.set_xlabel("Average Student Rating (1–5)", fontsize=11)
ax.set_title(f"All Faculties Ranked by Student Rating  (≥{MIN_REVIEWS} reviews per course)",
             fontsize=13)
ax.set_xlim(0, 6.5)
ax.invert_yaxis()
ax.tick_params(axis="y", length=0)
ax.axvline(np.mean(vals), color="black", linestyle="--", linewidth=1,
           alpha=0.5, label=f"Overall mean={np.mean(vals):.2f}")
ax.legend(fontsize=9)
plt.tight_layout()
plt.savefig(os.path.join(OUTPUT_DIR,"5_faculties_by_rating.png")); plt.close()
print("Saved: 5_faculties_by_rating.png")

# --- 5b: by grade ---
by_grade = sorted(fac_summary, key=lambda x: x[2])
fig, ax = plt.subplots(figsize=(10, max(5, len(by_grade)*0.45)))
vals   = [x[2] for x in by_grade]
labels = [f"{x[0]}  (n={x[3]})" for x in by_grade]
n      = len(vals)
colors = ["#d62728"]*3 + ["#f4a261"]*(n-6) + ["#2ca02c"]*3
ax.barh(range(n), vals, color=colors, alpha=0.85, edgecolor="white")
for i, (v, x) in enumerate(zip(vals, by_grade)):
    ax.text(v+0.3, i, f"{v:.1f}  (rating {x[1]:.2f})", va="center", fontsize=8.5)
ax.set_yticks(range(n))
ax.set_yticklabels(labels, fontsize=10)
ax.set_xlabel("Average Final Grade", fontsize=11)
ax.set_title(f"All Faculties Ranked by Avg Grade  (≥{MIN_REVIEWS} reviews per course)",
             fontsize=13)
ax.set_xlim(50, 115)
ax.invert_yaxis()
ax.tick_params(axis="y", length=0)
ax.axvline(np.mean(vals), color="black", linestyle="--", linewidth=1,
           alpha=0.5, label=f"Overall mean={np.mean(vals):.1f}")
ax.legend(fontsize=9)
plt.tight_layout()
plt.savefig(os.path.join(OUTPUT_DIR,"6_faculties_by_grade.png")); plt.close()
print("Saved: 6_faculties_by_grade.png")

print(f"\nAll plots saved to: {OUTPUT_DIR}")