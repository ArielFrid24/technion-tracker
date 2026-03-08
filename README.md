# Technion Course Tracker

A full-stack degree planning tool for Technion students studying **Data Science & Engineering**.  
Upload your transcript, track your progress, and get personalized semester recommendations.

---

## Features

- **Transcript parser** — upload your Technion תדפיס ציונים PDF and automatically import passed and failed courses
- **Degree progress tracking** — see exactly how many points you're missing per requirement category (חובה, נתונים, מדעי, מלג, ספורט, בחירה חופשית)
- **Smart semester recommender** — picks the best schedule for you based on:
  - Mandatory courses first (ordered by curriculum semester)
  - Then data-intensive, data electives, science, מלג, sport, free choice
  - Prerequisite checking — won't suggest courses you can't take yet
  - Category quota caps — won't over-suggest a category beyond what you still need
  - Failed course retake support — courses you failed stay eligible
- **Multiple schedule options** — choose from every valid point total in your range (e.g. 16, 16.5, 17... pts), each showing estimated weighted average
- **Must-take & blocklist** — lock in required courses and exclude full/unwanted ones before the recommender runs
- **Before/after degree progress** — see exactly what each schedule does to your remaining requirements

---

## Tech Stack

| Layer | Technology |
|---|---|
| Frontend | React, IBM Plex fonts, custom dark UI |
| Backend | Python, Flask, Flask-CORS |
| Scraping | Playwright (async), Firestore REST API |
| Data | CSV pipeline, Google Firebase |
| PDF parsing | pdf.js (CDN) |

---

## Project Structure

```
cheesefork_scraper/
│
├── app.py                        # Flask backend — recommender + degree progress API
├── cheesefork_scraper.py         # Main scraper — grade histograms + CheeseFork ratings
├── update_latest.py              # Scrapes latest semester course list from CheeseFork
├── update_grades.py              # Checks for new grades and updates CSVs
├── patch_rating.py               # Fills in missing CheeseFork ratings
│
├── scrape_courses.py             # Labeled data science electives
├── Mendatory_course_scraper.py   # Mandatory courses per semester
├── scrape_malag.py               # מלג courses
├── sports_scraper.py             # Sport courses
├── add_science.py                # Science courses
├── Scrape_free_choice.py         # Free choice courses
│
├── Parse_transcript.py           # CLI transcript PDF parser
├── Recomender.py                 # Standalone recommender (CLI)
├── analyze.py / top_courses.py   # Data analysis scripts
│
├── courses_labeled.csv           # Main course DB with grades, ratings, categories
├── courses_per_semester_all.csv  # Grade data per semester per course
├── courses_aggregated_all.csv    # Aggregated grade statistics
│
└── ui/                           # React frontend
    ├── src/App.js                # Full single-component React app
    └── public/
        └── courses_labeled.csv   # Served statically for frontend DB
```

---

## Getting Started

### Prerequisites
- Python 3.9+
- Node.js 16+
- Playwright: `pip install playwright && playwright install chromium`
- Flask: `pip install flask flask-cors`

### Run the app

**Terminal 1 — React frontend:**
```bash
cd ui
npm install
npm start
```

**Terminal 2 — Flask backend:**
```bash
pip install flask flask-cors
python app.py
```

Open [http://localhost:3000](http://localhost:3000)

### Update course data

```bash
# 1. Get latest semester course list
python update_latest.py

# 2. Pull new grades from histogram site
python update_grades.py

# 3. Refresh UI data
copy courses_labeled.csv ui\public\courses_labeled.csv
```

---

## How the Recommender Works

1. Loads all courses offered in the target semester from `semester_XXXXXX.json`
2. Filters to courses in the labeled DB with known credits and category
3. Checks prerequisites against your passed courses only (failed courses don't unlock prereqs)
4. Caps each category at what you still need for your degree
5. Sorts by priority tier, then by average historical grade within each tier
6. Greedy fills from top until reaching the target point total
7. Runs once per 0.5pt increment in your chosen range and deduplicates identical schedules

---

## Data Sources

- **Grade histograms** — [michael-maltsev/technion-histograms](https://github.com/michael-maltsev/technion-histograms)
- **Course ratings & listings** — [CheeseFork](https://cheesefork.cf/) (Firestore backend)

---

## Degree Requirements (Data Science & Engineering — 155 pts)

| Category | Required |
|---|---|
| חובה (Mandatory) | 108 pts |
| בחירה בנתונים (Data electives) | 24.5 pts |
| עתיר נתונים (Data-intensive) | 2 pts |
| בחירה פקולטית (Faculty electives) | 10.5 pts |
| קורס מדעי (Science) | 5.5 pts |
| מלג | 2 courses |
| ספורט | 2 courses |
| בחירה חופשית (Free choice) | 6 pts |
