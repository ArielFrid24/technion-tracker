import json, csv

with open("semester_202502.json") as f:
    sem = json.load(f)

db = set()
with open("courses_labeled.csv", encoding="utf-8-sig") as f:
    for row in csv.DictReader(f):
        db.add(row["course_id"])

overlap = set(sem["courses"]) & db
print("Semester courses:", len(sem["courses"]))
print("DB courses:", len(db))
print("Overlap:", len(overlap))
print("First 5 semester:", sem["courses"][:5])
print("First 5 DB:", sorted(db)[:5])