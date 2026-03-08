"""
parse_transcript.py
Parses a Technion transcript PDF and updates taken.json with passed courses.
Passing = numeric grade >= 55, or "Pass", or "Exemption with points"
Failing/excluded = numeric grade < 55, "Exemption without points", plain "Exemption"

Usage:
    python parse_transcript.py transcript.pdf
    python parse_transcript.py transcript.pdf --dry-run   # preview without saving
"""
import argparse, json, os, re, sys
import pdfplumber

OUTPUT_DIR = "C:/Users/pc/OneDrive - Technion/Desktop/cheesefork_scraper"
TAKEN_JSON = os.path.join(OUTPUT_DIR, "taken.json")

def parse_transcript(pdf_path):
    """Extract (course_id, grade_str, passed) tuples from transcript PDF."""
    text = ""
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text += page.extract_text() + "\n"

    # Each course line starts with an 8-digit course ID
    # Format: XXXXXXXX  <name>  <credits>  <grade>  <semester>
    # Grade can be: number, "Pass", "Exemption with points",
    #               "Exemption without points", "Exemption"
    results = []
    lines = text.splitlines()

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        # Match line starting with 8-digit course ID
        m = re.match(r'^(\d{8})\s+(.+)', line)
        if not m:
            i += 1
            continue

        cid = m.group(1)
        rest = m.group(2)

        # Sometimes name spills to next line — collect until we find grade info
        # Look ahead for continuation lines (no course ID, no grade pattern yet)
        full_text = rest
        while i + 1 < len(lines):
            next_line = lines[i+1].strip()
            # Stop if next line is another course ID or empty
            if re.match(r'^\d{8}\s', next_line) or not next_line:
                break
            # Stop if this line already has the grade in it
            if re.search(r'\d{4}-\d{4}', full_text):  # semester found
                break
            full_text += " " + next_line
            i += 1

        # Parse grade from full_text
        # Patterns to find:
        # "3.5  66  2022-2023 Spring"
        # "3.5  Pass  2022-2023 Spring"
        # "Exemption with points  2022-2023 Winter"
        # "Exemption without points  2022-2023 Winter"
        # "Exemption  2022-2023 Winter"

        grade_str = None
        passed = False

        # Try numeric grade
        m2 = re.search(r'\b(\d+(?:\.\d+)?)\s+(\d{1,3})\s+\d{4}-\d{4}', full_text)
        if m2:
            try:
                grade = float(m2.group(2))
                grade_str = str(int(grade))
                passed = grade >= 55
            except:
                pass

        # Try Pass
        if grade_str is None:
            if re.search(r'\bPass\b', full_text, re.IGNORECASE):
                grade_str = "Pass"
                passed = True

        # Try Exemption with points
        if grade_str is None:
            if re.search(r'Exemption with points', full_text, re.IGNORECASE):
                grade_str = "Exemption with points"
                passed = True

        # Try Exemption without points
        if grade_str is None:
            if re.search(r'Exemption without points', full_text, re.IGNORECASE):
                grade_str = "Exemption without points"
                passed = False  # no credit points

        # Plain Exemption (no credit)
        if grade_str is None:
            if re.search(r'\bExemption\b', full_text, re.IGNORECASE):
                grade_str = "Exemption"
                passed = False

        if grade_str:
            results.append((cid, grade_str, passed))

        i += 1

    return results

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf", help="Path to transcript PDF")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview without saving")
    parser.add_argument("--taken", help="Path to taken.json",
                        default=TAKEN_JSON)
    args = parser.parse_args()

    if not os.path.exists(args.pdf):
        print(f"ERROR: {args.pdf} not found"); sys.exit(1)

    print(f"Parsing {args.pdf}...\n")
    courses = parse_transcript(args.pdf)

    passed = [(cid, g) for cid, g, p in courses if p]
    failed = [(cid, g) for cid, g, p in courses if not p]

    print(f"{'─'*55}")
    print(f"  PASSED ({len(passed)} courses):")
    for cid, g in passed:
        print(f"    ✓  {cid}  [{g}]")

    print(f"\n  NOT COUNTED ({len(failed)} courses):")
    for cid, g in failed:
        print(f"    ✗  {cid}  [{g}]")
    print(f"{'─'*55}")

    if args.dry_run:
        print("\nDry run — nothing saved.")
        return

    # Load existing taken and merge
    existing = set()
    if os.path.exists(args.taken):
        with open(args.taken) as f:
            existing = set(json.load(f))

    new_ids = {cid for cid, _ in passed}
    added   = new_ids - existing
    merged  = existing | new_ids

    with open(args.taken, "w") as f:
        json.dump(sorted(merged), f, indent=2)

    print(f"\n  Added {len(added)} new courses to {args.taken}")
    print(f"  Total taken: {len(merged)} courses")

main()