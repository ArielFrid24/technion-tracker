import { useState, useRef, useCallback, useEffect } from "react";

// ─── PDF text extraction via pdf.js CDN ──────────────────────────────────────
async function extractPdfText(file) {
  await new Promise((resolve) => {
    if (window.pdfjsLib) return resolve();
    const script = document.createElement("script");
    script.src = "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.min.js";
    script.onload = () => {
      window.pdfjsLib.GlobalWorkerOptions.workerSrc =
        "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js";
      resolve();
    };
    document.head.appendChild(script);
  });

  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = async (e) => {
      try {
        const pdf = await window.pdfjsLib.getDocument({ data: e.target.result }).promise;
        let text = "";
        for (let i = 1; i <= pdf.numPages; i++) {
          const page = await pdf.getPage(i);
          const content = await page.getTextContent();
          // preserve spacing between items
          const pageText = content.items.map((item) => item.str).join(" ");
          text += pageText + "\n";
        }
        resolve(text);
      } catch (err) {
        reject(err);
      }
    };
    reader.readAsArrayBuffer(file);
  });
}

// ─── CSV parser for courses_labeled.csv ──────────────────────────────────────
function parseCoursesCsv(text) {
  const lines = text.split(/\r?\n/);
  if (lines.length < 2) return {};
  const headers = lines[0].split(",").map(h => h.trim().replace(/^\uFEFF/, ""));
  const idIdx      = headers.indexOf("course_id");
  const nameIdx    = headers.indexOf("course_name");
  const credIdx    = headers.indexOf("credits");
  const catIdx     = headers.indexOf("category");
  const gradeIdx   = headers.indexOf("avg_final_grade");
  const rankIdx    = headers.indexOf("avg_general_rank");
  const testIdx    = headers.indexOf("has_test");
  const db = {};
  for (let i = 1; i < lines.length; i++) {
    const row = lines[i].split(",");
    if (row.length < 2) continue;
    const cid = (row[idIdx] || "").trim();
    if (!cid) continue;
    db[cid] = {
      name:    (row[nameIdx]  || "").trim(),
      credits: parseFloat(row[credIdx]) || null,
      category:(row[catIdx]  || "").trim(),
      avgGrade:parseFloat(row[gradeIdx]) || null,
      avgRank: parseFloat(row[rankIdx])  || null,
      hasTest: testIdx >= 0 ? (row[testIdx] || "").trim() : "", // "1"/"0"/""
    };
  }
  return db;
}

// ─── Transcript parser — works on both line-based and flat pdf.js output ────────
function parseTranscript(text) {
  const results = [];

  // pdf.js in browser outputs flat text with spaces; split on 8-digit course IDs
  // Find all 8-digit IDs and their positions
  const idRegex = /\b(\d{8})\b/g;
  const matches = [];
  let m;
  while ((m = idRegex.exec(text)) !== null) {
    matches.push({ cid: m[1], index: m.index });
  }

  if (matches.length === 0) return results;

  for (let i = 0; i < matches.length; i++) {
    const { cid, index } = matches[i];
    const end = i + 1 < matches.length ? matches[i + 1].index : text.length;
    const chunk = text.slice(index + 8, end).trim(); // text after the course ID

    let gradeStr = null, passed = false, gradeNum = null;

    // numeric grade: "3.5 66 2022-2023" or "3.5 66 2022"
    const numM = chunk.match(/\b(\d+(?:\.\d+)?)\s+(\d{1,3})\s+\d{4}/);
    // Technion transcripts come in English ("Pass"/"Exemption ...") or Hebrew
    // ("עובר"/"פטור ..."). Binary-pass and exemption courses carry no numeric
    // grade at all, so both vocabularies have to be checked here or those
    // rows silently vanish (gradeStr stays null and the row is dropped below).
    if (numM) {
      gradeNum = parseFloat(numM[2]);
      gradeStr = String(Math.round(gradeNum));
      passed = gradeNum >= 55;
    } else if (/\bPass\b/i.test(chunk) || /עובר/.test(chunk)) {
      gradeStr = "Pass"; passed = true;
    } else if (/Exemption with points/i.test(chunk) || /פטור עם ניקוד/.test(chunk)) {
      gradeStr = "Exemption +pts"; passed = true;
    } else if (/Exemption without points/i.test(chunk) || /פטור ללא ניקוד/.test(chunk)) {
      gradeStr = "Exemption –pts"; passed = false;
    } else if (/\bExemption\b/i.test(chunk) || /פטור/.test(chunk)) {
      gradeStr = "Exemption"; passed = false;
    }

    // semester
    const semM = chunk.match(/(\d{4}-\d{4}\s+(?:Winter|Spring|Summer|חורף|אביב|קיץ)(?:\s+תש[^\s]*)?)/);
    const semester = semM ? semM[1] : "";

    // credits: number followed by grade+year OR Pass/Exemption
    // lookahead prevents matching "1" in "Statistics 1" or "Physics 1"
    const credM = chunk.match(/\b(\d+(?:\.\d+)?)\s+(?=\d{1,3}\s+\d{4}|Pass\b|Exemption\b|עובר|פטור)/);
    const credits = credM ? parseFloat(credM[1]) : null;

    // name: text before the credits match
    let name = credM
      ? chunk.slice(0, credM.index).trim()
      : chunk.replace(/\s+\d{4}-\d{4}.*$/, "").trim();
    name = name.replace(/\s+/g, " ").trim();
    // strip footer text that leaks in at page boundaries
    name = name.replace(/Transcript of .*/i, "").trim();
    name = name.replace(/\(E\):.*$/i, "").trim();
    name = name.replace(/Minimal Passing.*/i, "").trim();
    name = name.replace(/Grade Scale.*/i, "").trim();
    name = name.replace(/Haifa,.*$/i, "").trim();
    name = name.replace(/Page \d+ of \d+.*/i, "").trim();
    name = name.replace(/SUBJECT CREDITS.*/i, "").trim();
    // Hebrew equivalents of the above
    name = name.replace(/\(E\(:.*$/, "").trim();
    name = name.replace(/ציון מעבר מינימלי.*/, "").trim();
    name = name.replace(/סולם ציונים.*/, "").trim();
    name = name.replace(/חיפה,.*$/, "").trim();
    name = name.replace(/עמוד \d+ מתוך \d+.*/, "").trim();
    name = name.replace(/פטור עם ניקוד.*/, "").trim();
    name = name.replace(/פטור ללא ניקוד.*/, "").trim();
    name = name.replace(/עובר.*/, "").trim();
    name = name.replace(/פטור.*/, "").trim();

    if (gradeStr) {
      results.push({ cid, name, gradeStr, gradeNum, credits, semester, passed });
    }
  }
  return results;
}

// ─── Storage (in-memory; swap to localStorage for production) ─────────────────
function useTakenCourses() {
  const [taken, setTakenState] = useState({});
  const set = useCallback((updater) => {
    setTakenState((prev) => {
      const next = typeof updater === "function" ? updater(prev) : updater;
      // localStorage.setItem("technion_taken", JSON.stringify(next)); // enable locally
      return next;
    });
  }, []);
  return [taken, set];
}

// ─── Tiny components ──────────────────────────────────────────────────────────
const GradePill = ({ g }) => {
  if (!g) return null;
  const num = parseFloat(g);
  let bg = "#1e3a2a", color = "#6bc47a";
  if (!isNaN(num) && num < 55) { bg = "#3a1e1e"; color = "#c46b6b"; }
  if (g === "Exemption –pts" || g === "Exemption") { bg = "#1e1e1e"; color = "#555"; }
  if (g === "In Progress") { bg = "#0a1420"; color = "#6b9bc4"; }
  return (
    <span style={{ background: bg, color, borderRadius: 3, padding: "2px 8px", fontSize: 11, fontWeight: 600, letterSpacing: "0.04em" }}>
      {g}
    </span>
  );
};

const SourceDot = ({ s }) => {
  const label = s === "manual" ? "manual" : s === "current" ? "in progress" : "transcript";
  const color = s === "manual" ? "#c8a050" : s === "current" ? "#6b9bc4" : "#4a7a5a";
  return <span style={{ color, fontSize: 11 }}>{label}</span>;
};

// ─── Degree requirement category labels (mirrors REQ keys in app.py) ─────────
const REQ_LABELS = {
  "חובה":           "Mandatory (חובה)",
  "קורס מדעי":      "Science (מדעי)",
  "בחירה בנתונים":  "Data electives (נתונים)",
  "עתיר נתונים_n":  "Data-intensive (עתיר)",
  "בחירה פקולטית":  "Faculty electives",
  "ספורט_n":        "Sport courses",
  "מלג_n":          "מלג courses",
  "בחירה חופשית":   "Free choice (חופשית)",
  "total":          "Total credits",
};

// Maps a REQ_LABELS bucket key to the actual course category string stored
// in courses_labeled.csv (they differ for a few buckets — e.g. the CSV uses
// "קורסי בחירה בנתונים", not the bare "בחירה בנתונים" bucket name).
const REQ_TO_CATEGORY = {
  "חובה":           "חובה",
  "קורס מדעי":      "קורס מדעי",
  "בחירה בנתונים":  "קורסי בחירה בנתונים",
  "עתיר נתונים_n":  "עתיר נתונים",
  "בחירה פקולטית":  "קורסי בחירה פקולטיים",
  "ספורט_n":        "קורס ספורט",
  "מלג_n":          "מלג",
  "בחירה חופשית":   "בחירה חופשית",
};

// Categories the exam-date scraper actually covers (scrape_exam_dates.py) —
// exam preference toggles only make sense for these.
const EXAM_PREF_CATEGORIES = [
  { cat: "מלג",                   label: "מלג" },
  { cat: "קורסי בחירה בנתונים",  label: "Data electives (נתונים)" },
  { cat: "עתיר נתונים",          label: "Data-intensive (עתיר)" },
  { cat: "קורסי בחירה פקולטיים", label: "Faculty electives" },
  { cat: "בחירה חופשית",         label: "Free choice (חופשית)" },
];

// ─── Main App ─────────────────────────────────────────────────────────────────
export default function App() {
  const [taken, setTaken] = useTakenCourses();
  const [coursesDb, setCoursesDb] = useState({});
  const [dbLoading, setDbLoading] = useState(true);
  const [view, setView] = useState("home"); // home | upload | manual | review | csv

  // Auto-fetch courses_labeled.csv from public/ on startup
  useEffect(() => {
    fetch("/courses_labeled.csv")
      .then(r => { if (!r.ok) throw new Error("not found"); return r.text(); })
      .then(text => {
        const db = parseCoursesCsv(text);
        setCoursesDb(db);
        console.log(`Loaded ${Object.keys(db).length} courses from CSV`);
      })
      .catch(() => console.warn("courses_labeled.csv not found in public/"))
      .finally(() => setDbLoading(false));
  }, []);
  const [parsed, setParsed] = useState([]);
  const [selected, setSelected] = useState(new Set());
  const [drag, setDrag] = useState(false);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const [toast, setToast] = useState("");
  const [manualId, setManualId] = useState("");
  const [manualGrade, setManualGrade] = useState("");
  const [search, setSearch] = useState("");
  const [semInput, setSemInput] = useState("");
  const [semStatus, setSemStatus] = useState(null);
  const [semesterOptions, setSemesterOptions] = useState([]);
  const [minPts, setMinPts] = useState(9);
  const [maxPts, setMaxPts] = useState(12);
  const [planStep, setPlanStep] = useState("semester"); // semester | must | slider | results
  const [mustIds, setMustIds] = useState([]);
  const [mustInput, setMustInput] = useState("");
  const [mustStatus, setMustStatus] = useState(null);
  const [mustStatusLoading, setMustStatusLoading] = useState(false);
  const [browseCategory, setBrowseCategory] = useState(null); // course category string, or null when closed
  const [browseNextOnly, setBrowseNextOnly] = useState(true);
  const [semCourseCache, setSemCourseCache] = useState({}); // { [semesterCode]: Set of course ids }
  const [semCourseLoading, setSemCourseLoading] = useState(false);
  const [examPref, setExamPref] = useState({}); // { categoryString: "any" | "with" | "without" }
  const [blockIds, setBlockIds] = useState([]);
  const [blockInput, setBlockInput] = useState("");
  const [recommendation, setRecommendation] = useState(null);
  const [recOptions, setRecOptions] = useState([]);
  const [recLoading, setRecLoading] = useState(false);
  const [recError, setRecError] = useState("");
  const fileRef = useRef();
  const manualIdRef = useRef();

  const takenList = Object.entries(taken).sort(([a], [b]) => a.localeCompare(b));
  const takenCount = takenList.length;

  const flash = (msg) => { setToast(msg); setTimeout(() => setToast(""), 2800); };

  // ── CSV handling ─────────────────────────────────────────────────────────────
  const handleCsvFile = (file) => {
    if (!file?.name?.endsWith(".csv")) { setErr("Please upload a CSV file"); return; }
    const reader = new FileReader();
    reader.onload = (e) => {
      const db = parseCoursesCsv(e.target.result);
      const count = Object.keys(db).length;
      if (count === 0) { setErr("No courses found in CSV — is this courses_labeled.csv?"); return; }
      setCoursesDb(db);
      flash(`✓ Loaded ${count} courses from CSV`);
      setView("home");
    };
    reader.readAsText(file, "utf-8");
  };

  // ── PDF handling ────────────────────────────────────────────────────────────
  const handleFile = async (file) => {
    if (!file?.name?.endsWith(".pdf")) { setErr("Please upload a PDF file"); return; }
    setLoading(true); setErr("");
    try {
      const text = await extractPdfText(file);
      const courses = parseTranscript(text);
      if (!courses.length) throw new Error("No courses found — is this a Technion transcript?");
      // Enrich with CSV data
      const enriched = courses.map(c => {
        const db = coursesDb[c.cid] || {};
        return {
          ...c,
          name: db.name || c.name,
          credits: db.credits ?? c.credits,
          category: db.category || "",
        };
      });
      setParsed(enriched);
      setSelected(new Set(enriched.filter((c) => c.passed).map((c) => c.cid)));
      setView("review");
    } catch (e) {
      setErr("Parse error: " + e.message);
    } finally {
      setLoading(false);
    }
  };

  const onDrop = useCallback((e) => {
    e.preventDefault(); setDrag(false);
    handleFile(e.dataTransfer.files[0]);
  }, []);

  // ── Confirm reviewed courses ─────────────────────────────────────────────
  const confirmReview = () => {
    setTaken((prev) => {
      const next = { ...prev };
      parsed.forEach((c) => {
        if (selected.has(c.cid)) {
          next[c.cid] = { name: c.name, grade: c.gradeStr, credits: c.credits, semester: c.semester, source: "transcript", passed: c.passed !== false };
        }
      });
      return next;
    });
    // Auto-block exemption courses (no points) so they won't be recommended
    const exemptIds = parsed
      .filter(c => c.gradeStr && c.gradeStr.toLowerCase().includes("exemption without"))
      .map(c => c.cid);
    if (exemptIds.length > 0) {
      setBlockIds(prev => [...new Set([...prev, ...exemptIds])]);
    }
    flash(`✓ Saved ${selected.size} courses from transcript${exemptIds.length > 0 ? ` · ${exemptIds.length} exemptions auto-blocked` : ""}`);
    setView("home");
  };

  // ── Manual add ──────────────────────────────────────────────────────────
  const addManual = () => {
    const id = manualId.trim().padStart(8, "0");
    if (!/^\d{8}$/.test(id)) { setErr("Course ID must be 7-8 digits"); return; }
    const dbEntry = coursesDb[id] || {};
    setTaken((prev) => ({
      ...prev,
      [id]: {
        name: dbEntry.name || "—",
        grade: manualGrade.trim() || "Pass",
        credits: dbEntry.credits ?? null,
        category: dbEntry.category || "",
        semester: "",
        source: "manual",
        passed: true,
      },
    }));
    flash(`✓ Added ${id}`);
    setManualId(""); setManualGrade(""); setErr("");
    manualIdRef.current?.focus();
  };

  const removeCourse = (cid) => {
    setTaken((prev) => { const n = { ...prev }; delete n[cid]; return n; });
  };

  // ── Filtered list ───────────────────────────────────────────────────────
  const filtered = takenList.filter(([cid, info]) =>
    !search || cid.includes(search) || info.name.toLowerCase().includes(search.toLowerCase())
  );

  // ─── Styles ────────────────────────────────────────────────────────────────
  const css = `
    @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@300;400;500&display=swap');
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { background: #080a0c; }
    ::selection { background: #c8b56030; }
    ::-webkit-scrollbar { width: 6px; }
    ::-webkit-scrollbar-track { background: #0d0f12; }
    ::-webkit-scrollbar-thumb { background: #2a2e35; border-radius: 3px; }
    input:focus { outline: none; border-color: #c8b560 !important; }
    .row-hover:hover { background: #111418 !important; }
    .card-hover { transition: border-color .15s, transform .12s; cursor: pointer; }
    .card-hover:hover { border-color: #c8b560 !important; transform: translateY(-1px); }
    .btn-primary { transition: opacity .12s, transform .1s; }
    .btn-primary:hover { opacity: .85; transform: translateY(-1px); }
    .btn-ghost:hover { border-color: #c8b560 !important; color: #c8b560 !important; }
    .remove-btn:hover { color: #e05050 !important; }
    @keyframes fadeUp { from { opacity:0; transform:translateY(12px);} to { opacity:1; transform:none;} }
    .fade-up { animation: fadeUp .25s ease both; }
    @keyframes toastIn { from {opacity:0;transform:translate(-50%,8px);} to {opacity:1;transform:translate(-50%,0);} }
    .toast-anim { animation: toastIn .2s ease both; }
    .he { direction: rtl; text-align: right; unicode-bidi: embed; }
    @keyframes spin { from {transform:rotate(0deg);} to {transform:rotate(360deg);} }
    .spin { animation: spin 1s linear infinite; display:inline-block; }
    @keyframes pulse { 0%,100%{opacity:.4;} 50%{opacity:1;} }
    .pulse-dot { animation: pulse 1.2s ease-in-out infinite; }
  `;

  const V = {
    wrap: { minHeight: "100vh", background: "#080a0c", color: "#d8d4cc", fontFamily: "'IBM Plex Mono', monospace" },
    header: { borderBottom: "1px solid #151a20", padding: "16px 32px", display: "flex", alignItems: "center", justifyContent: "space-between", background: "#080a0c", position: "sticky", top: 0, zIndex: 10 },
    logo: { fontSize: 12, letterSpacing: "0.18em", textTransform: "uppercase", color: "#5a6575" },
    logoAccent: { color: "#c8b560" },
    pill: { background: "#111418", border: "1px solid #1e2530", borderRadius: 4, padding: "3px 12px", fontSize: 11, color: "#4a5060", letterSpacing: "0.06em" },
    main: { maxWidth: 820, margin: "0 auto", padding: "48px 24px 80px" },
    h1: { fontSize: 26, fontWeight: 400, letterSpacing: "-0.02em", color: "#e8e4da", marginBottom: 6, fontFamily: "'IBM Plex Sans', sans-serif" },
    sub: { fontSize: 12, color: "#5a6575", marginBottom: 40, letterSpacing: "0.04em" },
    grid2: { display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14, marginBottom: 36 },
    card: { background: "#0c0e12", border: "1px solid #181c24", borderRadius: 8, padding: "22px 24px" },
    cardLabel: { fontSize: 10, letterSpacing: "0.14em", textTransform: "uppercase", color: "#c8b560", marginBottom: 8 },
    cardDesc: { fontSize: 12, color: "#5a6575", lineHeight: 1.7 },
    section: { background: "#0c0e12", border: "1px solid #181c24", borderRadius: 8, padding: "24px", marginBottom: 20 },
    secTitle: { fontSize: 10, letterSpacing: "0.14em", textTransform: "uppercase", color: "#5a6575", marginBottom: 16 },
    table: { width: "100%", borderCollapse: "collapse" },
    th: { fontSize: 10, letterSpacing: "0.1em", textTransform: "uppercase", color: "#4a5565", padding: "6px 10px", textAlign: "left", borderBottom: "1px solid #141820" },
    td: { padding: "9px 10px", fontSize: 12, borderBottom: "1px solid #0f1318", verticalAlign: "middle" },
    dropzone: { border: "1.5px dashed #1e2530", borderRadius: 8, padding: "52px 24px", textAlign: "center", cursor: "pointer", transition: "border-color .15s, background .15s" },
    dropActive: { borderColor: "#c8b560", background: "#0f1108" },
    btnPrimary: { background: "#c8b560", color: "#080a0c", border: "none", borderRadius: 4, padding: "10px 22px", fontSize: 12, fontFamily: "'IBM Plex Mono', monospace", fontWeight: 600, letterSpacing: "0.06em", cursor: "pointer" },
    btnGhost: { background: "transparent", color: "#5a6575", border: "1px solid #1e2530", borderRadius: 4, padding: "10px 22px", fontSize: 12, fontFamily: "'IBM Plex Mono', monospace", cursor: "pointer", transition: "all .15s" },
    input: { background: "#080a0c", border: "1px solid #1e2530", borderRadius: 4, padding: "9px 12px", fontSize: 12, fontFamily: "'IBM Plex Mono', monospace", color: "#d8d4cc", width: "100%", transition: "border-color .15s" },
    back: { fontSize: 11, color: "#5a6575", cursor: "pointer", letterSpacing: "0.08em", marginBottom: 32, display: "inline-flex", alignItems: "center", gap: 6, transition: "color .12s" },
    err: { background: "#160b0b", border: "1px solid #3a1414", borderRadius: 4, padding: "10px 14px", fontSize: 12, color: "#c46b6b", marginBottom: 16 },
    toast: { position: "fixed", bottom: 28, left: "50%", transform: "translateX(-50%)", background: "#c8b560", color: "#080a0c", padding: "9px 22px", borderRadius: 4, fontSize: 12, fontWeight: 600, letterSpacing: "0.06em", zIndex: 999 },
  };

  // ─── REVIEW view ─────────────────────────────────────────────────────────
  if (view === "review") {
    const passedC = parsed.filter((c) => c.passed);
    const failedC = parsed.filter((c) => !c.passed);
    const toggle = (cid) => {
      setSelected((s) => { const n = new Set(s); n.has(cid) ? n.delete(cid) : n.add(cid); return n; });
    };
    return (
      <div style={V.wrap}>
        <style>{css}</style>
        <div style={V.header}>
          <span style={V.logo}>TECHNION <span style={V.logoAccent}>TRACKER</span></span>
          <span style={V.pill}>{selected.size} selected</span>
        </div>
        <div style={V.main}>
          <div className="fade-up">
            <span className="back" style={V.back} onClick={() => setView("home")}>← back</span>
            <div style={V.h1}>Review transcript</div>
            <div style={V.sub}>{passedC.length} passed · {failedC.length} excluded — deselect any to skip</div>

            <div style={V.section}>
              <div style={V.secTitle}>Passed — will be saved</div>
              <table style={V.table}>
                <thead><tr>
                  <th style={V.th}></th>
                  <th style={V.th}>Course ID</th>
                  <th style={V.th}>Name</th>
                  <th style={V.th}>Credits</th>
                  <th style={V.th}>Grade</th>
                  <th style={V.th}>Semester</th>
                </tr></thead>
                <tbody>
                  {passedC.map((c) => (
                    <tr key={c.cid} className="row-hover" onClick={() => toggle(c.cid)}
                      style={{ cursor: "pointer", background: selected.has(c.cid) ? "#0d1008" : "transparent", transition: "background .1s" }}>
                      <td style={V.td}>
                        <input type="checkbox" checked={selected.has(c.cid)} onChange={() => toggle(c.cid)}
                          style={{ accentColor: "#c8b560", cursor: "pointer" }} onClick={(e) => e.stopPropagation()} />
                      </td>
                      <td style={{ ...V.td, color: "#c8b560", letterSpacing: "0.05em" }}>{c.cid}</td>
                      <td style={{ ...V.td, color: "#7a8090", maxWidth: 240 }}>{c.name}</td>
                      <td style={{ ...V.td, color: "#4a5060" }}>{c.credits ?? "—"}</td>
                      <td style={V.td}><GradePill g={c.gradeStr} /></td>
                      <td style={{ ...V.td, color: "#5a6575", fontSize: 11 }}>{c.semester}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {failedC.length > 0 && (
              <div style={{ ...V.section, opacity: 0.6 }}>
                <div style={V.secTitle}>Excluded — failed / no credit</div>
                <table style={V.table}>
                  <thead><tr>
                    <th style={V.th}>Course ID</th>
                    <th style={V.th}>Name</th>
                    <th style={V.th}>Grade</th>
                  </tr></thead>
                  <tbody>
                    {failedC.map((c) => (
                      <tr key={c.cid} style={V.td}>
                        <td style={{ ...V.td, color: "#5a6575" }}>{c.cid}</td>
                        <td style={{ ...V.td, color: "#4a5565" }}>{c.name}</td>
                        <td style={V.td}><GradePill g={c.gradeStr} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            <div style={{ display: "flex", gap: 12 }}>
              <button className="btn-primary" style={V.btnPrimary} onClick={confirmReview}>
                Save {selected.size} courses →
              </button>
              <button className="btn-ghost" style={V.btnGhost} onClick={() => setView("home")}>Cancel</button>
            </div>
          </div>
        </div>
        {toast && <div className="toast-anim" style={V.toast}>{toast}</div>}
      </div>
    );
  }

  // ─── UPLOAD view ──────────────────────────────────────────────────────────
  if (view === "upload") {
    return (
      <div style={V.wrap}>
        <style>{css}</style>
        <div style={V.header}>
          <span style={V.logo}>TECHNION <span style={V.logoAccent}>TRACKER</span></span>
        </div>
        <div style={V.main}>
          <div className="fade-up">
            <span style={V.back} onClick={() => { setView("home"); setErr(""); }}>← back</span>
            <div style={V.h1}>Upload transcript</div>
            <div style={V.sub}>Import your official Technion תדפיס ציונים PDF</div>
            {err && <div style={V.err}>{err}</div>}
            <div style={V.section}>
              <div
                style={{ ...V.dropzone, ...(drag ? V.dropActive : {}) }}
                onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
                onDragLeave={() => setDrag(false)}
                onDrop={onDrop}
                onClick={() => fileRef.current.click()}
              >
                <input ref={fileRef} type="file" accept=".pdf" style={{ display: "none" }}
                  onChange={(e) => handleFile(e.target.files[0])} />
                {loading ? (
                  <div style={{ color: "#5a6575", fontSize: 13 }}>
                    <div style={{ fontSize: 28, marginBottom: 12, opacity: 0.4 }}>⟳</div>
                    Parsing PDF...
                  </div>
                ) : (
                  <>
                    <div style={{ fontSize: 36, marginBottom: 14, opacity: 0.25 }}>↑</div>
                    <div style={{ fontSize: 13, color: "#4a5060" }}>Drop PDF here or click to browse</div>
                    <div style={{ fontSize: 11, color: "#4a5565", marginTop: 8, letterSpacing: "0.06em" }}>
                      TECHNION TRANSCRIPT · תדפיס ציונים
                    </div>
                  </>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>
    );
  }

  // ─── MANUAL view ──────────────────────────────────────────────────────────
  if (view === "manual") {
    return (
      <div style={V.wrap}>
        <style>{css}</style>
        <div style={V.header}>
          <span style={V.logo}>TECHNION <span style={V.logoAccent}>TRACKER</span></span>
          <span style={V.pill}>{takenCount} courses</span>
        </div>
        <div style={V.main}>
          <div className="fade-up">
            <span style={V.back} onClick={() => { setView("home"); setErr(""); }}>← back</span>
            <div style={V.h1}>Add courses manually</div>
            <div style={V.sub}>Add courses not yet showing in your transcript</div>
            {err && <div style={V.err}>{err}</div>}

            <div style={V.section}>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 160px", gap: 12, marginBottom: 14 }}>
                <div>
                  <div style={{ ...V.secTitle, marginBottom: 6 }}>Course ID *</div>
                  <input ref={manualIdRef} style={V.input} placeholder="e.g. 00960411" value={manualId} autoComplete="off"
                    onChange={(e) => setManualId(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && addManual()} />
                </div>
                <div>
                  <div style={{ ...V.secTitle, marginBottom: 6 }}>Grade</div>
                  <input style={V.input} placeholder="85 or Pass" value={manualGrade} autoComplete="off"
                    onChange={(e) => setManualGrade(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && addManual()} />
                </div>
              </div>
              <button className="btn-primary" style={V.btnPrimary} onClick={addManual}>Add →</button>

              {takenList.filter(([, info]) => info.source === "manual").length > 0 && (
                <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 16 }}>
                  {takenList.filter(([, info]) => info.source === "manual").map(([cid, info]) => (
                    <span key={cid} style={{ display: "flex", alignItems: "center", gap: 6,
                      fontSize: 11, padding: "4px 10px", borderRadius: 3,
                      background: "#0a1420", border: "1px solid #1a3a4a", color: "#6b9bc4" }}>
                      <span className="he">{info.name || cid}</span>
                      <span style={{ cursor: "pointer", color: "#4a7a9a" }} onClick={() => removeCourse(cid)}>✕</span>
                    </span>
                  ))}
                </div>
              )}
            </div>

            {takenCount > 0 && (
              <div style={V.section}>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 14 }}>
                  <div style={V.secTitle}>All saved courses ({takenCount})</div>
                  <input style={{ ...V.input, width: 200, padding: "6px 10px" }}
                    placeholder="Search…" value={search} onChange={(e) => setSearch(e.target.value)} />
                </div>
                <table style={V.table}>
                  <thead><tr>
                    <th style={V.th}>Course ID</th>
                    <th style={V.th}>Name</th>
                    <th style={V.th}>Grade</th>
                    <th style={V.th}>Source</th>
                    <th style={V.th}></th>
                  </tr></thead>
                  <tbody>
                    {filtered.map(([cid, info]) => (
                      <tr key={cid} className="row-hover">
                        <td style={{ ...V.td, color: "#c8b560", letterSpacing: "0.05em" }}>{cid}</td>
                        <td style={{ ...V.td, color: "#6a7080", maxWidth: 260, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{info.name}</td>
                        <td style={V.td}><GradePill g={info.grade} /></td>
                        <td style={V.td}><SourceDot s={info.source} /></td>
                        <td style={{ ...V.td, textAlign: "right" }}>
                          <span className="remove-btn" style={{ color: "#3a2020", cursor: "pointer", fontSize: 11, letterSpacing: "0.06em" }}
                            onClick={() => removeCourse(cid)}>remove</span>
                        </td>
                      </tr>
                    ))}
                    {filtered.length === 0 && (
                      <tr><td colSpan={5} style={{ ...V.td, color: "#4a5565", textAlign: "center", padding: "24px" }}>no courses match</td></tr>
                    )}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
        {toast && <div className="toast-anim" style={V.toast}>{toast}</div>}
      </div>
    );
  }

  // ─── PLAN view ───────────────────────────────────────────────────────────────
  if (view === "plan") {

    const checkSemester = () => {
      const sem = semInput.trim();
      if (!/^\d{6}$/.test(sem)) { setErr("Semester must be 6 digits, e.g. 202502"); return; }
      setErr("");
      setSemStatus("checking");
      fetch(`http://localhost:5000/api/available?semester=${sem}`)
        .then(r => { if (!r.ok) throw new Error(); return r.json(); })
        .then(data => { setSemStatus(data.count > 0 ? "ready" : "not_ready"); })
        .catch(() => { setSemStatus(Object.keys(coursesDb).length > 0 ? "ready" : "not_ready"); });
    };

    const semLabel = (s) => {
      if (!s || s.length < 6) return s;
      const y = s.slice(0, 4), t = s.slice(4);
      if (t === "01") return `Winter ${y.slice(2)}`;
      if (t === "02") return `Spring ${String(parseInt(y)+1).slice(2)}`;
      if (t === "03") return `Summer ${String(parseInt(y)+1).slice(2)}`;
      return s;
    };

    const mustPts = mustIds.reduce((sum, cid) => {
      const c = coursesDb[cid];
      return sum + (c ? (c.credits || 0) : 0);
    }, 0);

    const addBlock = () => {
      const id = blockInput.trim().padStart(8, "0");
      if (!/^\d{8}$/.test(id)) { setErr("Course ID must be 8 digits"); return; }
      if (blockIds.includes(id)) { setErr("Already blocked"); return; }
      setBlockIds(prev => [...prev, id]);
      setBlockInput("");
      setErr("");
    };
    const removeBlock = (id) => setBlockIds(prev => prev.filter(x => x !== id));

    const addMust = () => {
      const id = mustInput.trim().padStart(8, "0");
      if (!/^\d{8}$/.test(id)) { setErr("Course ID must be 8 digits"); return; }
      if (mustIds.includes(id)) { setErr("Already added"); return; }
      if (!coursesDb[id]) { setErr(`Course ${id} not found in database`); return; }
      setMustIds(prev => [...prev, id]);
      setMustInput("");
      setErr("");
    };

    const removeMust = (id) => setMustIds(prev => prev.filter(x => x !== id));

    const findSchedule = async () => {
      setRecLoading(true); setRecError("");
      try {
        const res = await fetch("http://localhost:5000/api/recommend", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            semester: semInput,
            taken:  Object.keys(taken).filter(id => taken[id].passed !== false),
            failed: Object.keys(taken).filter(id => taken[id].passed === false),
            must:   mustIds,
            block:  blockIds,
            min:    minPts,
            max:    maxPts,
            examPref,
          })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || "No schedule found");
        const statusBefore = await fetch("http://localhost:5000/api/status", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ taken: Object.keys(taken).filter(id => taken[id].passed !== false) })
        }).then(r => r.json());
        setRecOptions(data.options.map(opt => ({ ...opt, statusBefore })));
        setPlanStep("options");
      } catch (e) {
        setRecError(e.message);
      } finally {
        setRecLoading(false);
      }
    };

    const selectOption = async (opt) => {
      const statusAfter = await fetch("http://localhost:5000/api/status", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ taken: [...Object.keys(taken).filter(id => taken[id].passed !== false), ...opt.courses.map(c => c.id)] })
      }).then(r => r.json());
      setRecommendation({ ...opt, statusAfter, statusBefore: opt.statusBefore });
      setPlanStep("results");
    };

        const goBack = () => {
      setView("home");
      setErr("");
      setSemStatus(null);
      setSemesterOptions([]);
      setPlanStep("semester");
      setMustIds([]);
      setMustInput("");
      setMustStatus(null);
      setBrowseCategory(null);
      setExamPref({});
      setBlockIds([]);
      setBlockInput("");
      setRecommendation(null);
      setRecError("");
    };

    const catColor = (cat) => {
      if (!cat) return "#4a5060";
      if (cat.includes("חובה") || cat.includes("מדעי")) return "#c8b560";
      if (cat.includes("נתונים") || cat.includes("עתיר")) return "#6b9bc4";
      if (cat.includes("פקולט")) return "#a06bc4";
      if (cat.includes("ספורט")) return "#6bc47a";
      if (cat.includes("מלג"))   return "#c46b6b";
      return "#4a5060";
    };

    // ── Step: semester ──────────────────────────────────────────────────────
    if (planStep === "semester") {
      if (semesterOptions.length === 0 && semStatus !== "loading_list") {
        setSemStatus("loading_list");
        fetch("http://localhost:5000/api/semesters")
          .then(r => r.json())
          .then(data => {
            setSemesterOptions(data.semesters || []);
            if (data.semesters && data.semesters.length > 0) setSemInput(data.semesters[0].code);
            setSemStatus(null);
          })
          .catch(() => setSemStatus(null));
      }
      return (
        <div style={V.wrap}>
          <style>{css}</style>
          <div style={V.header}>
            <span style={V.logo}>TECHNION <span style={V.logoAccent}>TRACKER</span></span>
            <span style={V.pill}>{takenCount} courses saved</span>
          </div>
          <div style={V.main}>
            <div className="fade-up">
              <span style={V.back} onClick={goBack}>← back</span>
              <div style={V.h1}>Plan next semester</div>
              <div style={V.sub}>Step 1 of 3 — select semester</div>
              {err && <div style={V.err}>{err}</div>}
              <div style={V.section}>
                <div style={{ ...V.secTitle, marginBottom: 12 }}>Semester</div>
                {semStatus === "loading_list" ? (
                  <div style={{ fontSize: 12, color: "#5a6575" }}>Loading semesters...</div>
                ) : semesterOptions.length > 0 ? (
                  <>
                    <select value={semInput} onChange={e => setSemInput(e.target.value)}
                      style={{ ...V.input, width: 260, cursor: "pointer", paddingRight: 36, appearance: "none", WebkitAppearance: "none" }}>
                      {semesterOptions.map(s => (
                        <option key={s.code} value={s.code} style={{ background: "#0c0e12", color: "#c8b560" }}>{s.label}</option>
                      ))}
                    </select>
                    <div style={{ marginTop: 20 }}>
                      <button className="btn-primary" style={V.btnPrimary}
                        onClick={() => { setPlanStep("must"); setErr(""); }}>
                        Next: add required courses →
                      </button>
                    </div>
                  </>
                ) : (
                  <div style={{ padding: "12px 16px", background: "#0f0e08", border: "1px solid #3a3010", borderRadius: 6 }}>
                    <div style={{ fontSize: 13, color: "#8a7a30" }}>⏳ No semester data found — run update_latest.py first</div>
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
      );
    }

        // ── Step: must-take ─────────────────────────────────────────────────────
    if (planStep === "must") {
      if (mustStatus === null && !mustStatusLoading) {
        setMustStatusLoading(true);
        fetch("http://localhost:5000/api/status", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ taken: Object.keys(taken).filter(id => taken[id].passed !== false) })
        }).then(r => r.json())
          .then(data => setMustStatus(data))
          .catch(() => setMustStatus({}))
          .finally(() => setMustStatusLoading(false));
      }

      const missing = mustStatus?.missing || {};
      const reqRows = Object.keys(REQ_LABELS).filter(k => k !== "total");

      if (browseCategory && browseNextOnly && !semCourseCache[semInput] && !semCourseLoading) {
        setSemCourseLoading(true);
        fetch(`http://localhost:5000/api/semester-courses?semester=${semInput}`)
          .then(r => r.json())
          .then(data => setSemCourseCache(prev => ({ ...prev, [semInput]: new Set(data.courses || []) })))
          .catch(() => setSemCourseCache(prev => ({ ...prev, [semInput]: new Set() })))
          .finally(() => setSemCourseLoading(false));
      }

      const browseRows = browseCategory
        ? Object.entries(coursesDb)
            .filter(([id, c]) => c.category === browseCategory && !taken[id])
            .filter(([id]) => !browseNextOnly || (semCourseCache[semInput] && semCourseCache[semInput].has(id)))
            .sort((a, b) => (b[1].avgGrade || 0) - (a[1].avgGrade || 0))
        : [];

      return (
      <div style={V.wrap}>
        <style>{css}</style>
        <div style={V.header}>
          <span style={V.logo}>TECHNION <span style={V.logoAccent}>TRACKER</span></span>
          <span style={V.pill}>{semLabel(semInput)}</span>
        </div>
        <div style={V.main}>
          <div className="fade-up">
            <span style={V.back} onClick={() => { setPlanStep("semester"); setErr(""); }}>← back</span>
            <div style={V.h1}>Required courses</div>
            <div style={V.sub}>Step 2 of 3 — add courses you must take this semester (optional)</div>
              <div style={{ fontSize: 11, color: "#3a5040", marginTop: 6, marginBottom: 4 }}>💡 Have an exemption for a course like אנגלית טכנית? Add it to the <span style={{color:"#c8b560"}}>exclude list</span> below so it won't be recommended</div>
            {err && <div style={V.err}>{err}</div>}

            {mustStatus && (
              <div style={V.section}>
                <div style={{ ...V.secTitle, marginBottom: 12 }}>What you still need for your degree</div>
                {reqRows.map(k => {
                  const done = (missing[k] || 0) <= 0;
                  const cat = REQ_TO_CATEGORY[k];
                  return (
                    <div key={k} style={{ display: "flex", justifyContent: "space-between", fontSize: 12, color: "#8a8a95", marginBottom: 8 }}>
                      <span className="he" style={{ cursor: "pointer", borderBottom: "1px dotted #3a4048" }}
                        onClick={() => setBrowseCategory(cat)}>
                        {REQ_LABELS[k]}
                      </span>
                      <span style={{ color: done ? "#6bc47a" : "#c8b560", fontWeight: 600 }}>
                        {done ? "✓ done" : `${missing[k]} ${k.endsWith("_n") ? (missing[k] === 1 ? "course" : "courses") : "pts"} needed`}
                      </span>
                    </div>
                  );
                })}
                <div style={{ fontSize: 10, color: "#4a5565", marginTop: 4 }}>Click any category to browse its courses</div>
                <div style={{ fontSize: 10, color: "#5a6575", marginTop: 14, lineHeight: 1.6, borderTop: "1px solid #181c24", paddingTop: 10 }}>
                  ⚠ This is calculated automatically from your saved courses and could be wrong — please double-check against your official degree audit. We did our best to get it right.
                </div>
              </div>
            )}

            <div style={V.section}>
              <div style={{ ...V.secTitle, marginBottom: 8 }}>Add a required course</div>
              <div style={{ display: "flex", gap: 12, marginBottom: 16 }}>
                <input style={{ ...V.input, width: 200 }} placeholder="e.g. 00960411" autoComplete="off"
                  value={mustInput}
                  onChange={e => setMustInput(e.target.value)}
                  onKeyDown={e => e.key === "Enter" && addMust()} />
                <button className="btn-primary" style={V.btnPrimary} onClick={addMust}>Add →</button>
              </div>

              {mustIds.length > 0 ? (
                <>
                  <table style={V.table}>
                    <thead><tr>
                      <th style={V.th}>Course ID</th>
                      <th style={V.th}>Name</th>
                      <th style={V.th}>Credits</th>
                      <th style={V.th}></th>
                    </tr></thead>
                    <tbody>
                      {mustIds.map(cid => {
                        const c = coursesDb[cid] || {};
                        return (
                          <tr key={cid} className="row-hover">
                            <td style={{ ...V.td, color: "#c8b560" }}>{cid}</td>
                            <td style={{ ...V.td, color: "#7a8090" }} className="he">{c.name || "—"}</td>
                            <td style={{ ...V.td, color: "#4a5060" }}>{c.credits ?? "—"}</td>
                            <td style={{ ...V.td, textAlign: "right" }}>
                              <span className="remove-btn" style={{ color: "#3a2020", cursor: "pointer", fontSize: 11 }}
                                onClick={() => removeMust(cid)}>remove</span>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                  <div style={{ marginTop: 14, fontSize: 12, color: "#c8b560" }}>
                    {mustPts} pts locked in
                  </div>
                </>
              ) : (
                <div style={{ fontSize: 12, color: "#4a5565", padding: "16px 0" }}>
                  No required courses added — skip to set your point range
                </div>
              )}
            </div>

              <div style={{ marginTop: 28 }}>
                <div style={{ ...V.secTitle, marginBottom: 8 }}>Courses to exclude</div>
                <div style={{ fontSize: 11, color: "#4a5565", marginBottom: 10 }}>
                  Full, no interest, or any other reason — the recommender will skip these
                </div>
                <div style={{ display: "flex", gap: 12, marginBottom: 12 }}>
                  <input style={{ ...V.input, width: 200 }} placeholder="e.g. 00960411" autoComplete="off"
                    value={blockInput}
                    onChange={e => setBlockInput(e.target.value)}
                    onKeyDown={e => e.key === "Enter" && addBlock()} />
                  <button className="btn-ghost" style={{ ...V.btnGhost, borderColor:"#3a2020", color:"#7a3030" }}
                    onClick={addBlock}>Block →</button>
                </div>
                {blockIds.length > 0 && (
                  <div style={{ display:"flex", flexWrap:"wrap", gap:6, marginBottom:8 }}>
                    {blockIds.map(cid => {
                      const c = coursesDb[cid] || {};
                      return (
                        <span key={cid} style={{ display:"flex", alignItems:"center", gap:6,
                          fontSize:11, padding:"4px 10px", borderRadius:3,
                          background:"#120a0a", border:"1px solid #3a1a1a", color:"#7a3030" }}>
                          <span className="he">{c.name || cid}</span>
                          <span style={{ cursor:"pointer", color:"#4a2020" }} onClick={() => removeBlock(cid)}>✕</span>
                        </span>
                      );
                    })}
                  </div>
                )}
              </div>

            <button className="btn-primary" style={{ ...V.btnPrimary, marginTop: 20 }}
              onClick={() => { setPlanStep("slider"); setErr(""); }}>
              Next: set point range →
            </button>
          </div>
        </div>
        {toast && <div className="toast-anim" style={V.toast}>{toast}</div>}

        {browseCategory && (
          <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.7)", zIndex: 100,
            display: "flex", alignItems: "center", justifyContent: "center", padding: 24 }}
            onClick={() => setBrowseCategory(null)}>
            <div style={{ background: "#0c0e12", border: "1px solid #1e2530", borderRadius: 8,
              padding: 24, maxWidth: 640, width: "100%", maxHeight: "80vh", display: "flex", flexDirection: "column" }}
              onClick={e => e.stopPropagation()}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
                <div style={{ fontSize: 15, color: "#e8e4da" }} className="he">{browseCategory}</div>
                <span style={{ cursor: "pointer", color: "#5a6575", fontSize: 12 }} onClick={() => setBrowseCategory(null)}>✕ close</span>
              </div>
              <div style={{ fontSize: 11, color: "#5a6575", marginBottom: 14 }}>Courses you haven't taken yet</div>

              <div style={{ display: "flex", gap: 8, marginBottom: 14 }}>
                <button
                  onClick={() => setBrowseNextOnly(true)}
                  style={{ ...V.btnGhost, padding: "6px 14px", fontSize: 11,
                    borderColor: browseNextOnly ? "#c8b560" : "#1e2530", color: browseNextOnly ? "#c8b560" : "#5a6575" }}>
                  Offered {semLabel(semInput)}
                </button>
                <button
                  onClick={() => setBrowseNextOnly(false)}
                  style={{ ...V.btnGhost, padding: "6px 14px", fontSize: 11,
                    borderColor: !browseNextOnly ? "#c8b560" : "#1e2530", color: !browseNextOnly ? "#c8b560" : "#5a6575" }}>
                  All courses
                </button>
              </div>

              <div style={{ overflow: "auto" }}>
                {browseNextOnly && semCourseLoading ? (
                  <div style={{ fontSize: 12, color: "#5a6575", padding: "16px 0" }}>Loading...</div>
                ) : browseRows.length === 0 ? (
                  <div style={{ fontSize: 12, color: "#4a5565", padding: "16px 0" }}>
                    No courses found{browseNextOnly ? " for this semester" : ""} — try "All courses" instead
                  </div>
                ) : (
                  <table style={V.table}>
                    <thead><tr>
                      <th style={V.th}>Course ID</th>
                      <th style={V.th}>Name</th>
                      <th style={V.th}>Credits</th>
                      <th style={V.th}>Avg grade</th>
                      <th style={V.th}>Avg rating</th>
                      <th style={V.th}>Exam</th>
                      <th style={V.th}>Must</th>
                      <th style={V.th}>Exclude</th>
                    </tr></thead>
                    <tbody>
                      {browseRows.map(([id, c]) => {
                        const isMust = mustIds.includes(id);
                        const isBlocked = blockIds.includes(id);
                        return (
                          <tr key={id} className="row-hover">
                            <td style={{ ...V.td, color: "#c8b560", letterSpacing: "0.05em" }}>{id}</td>
                            <td style={{ ...V.td, color: "#9a9090" }} className="he">{c.name}</td>
                            <td style={{ ...V.td, color: "#4a5060" }}>{c.credits ?? "—"}</td>
                            <td style={{ ...V.td, color: c.avgGrade ? "#6bc47a" : "#5a6575" }}>{c.avgGrade ? c.avgGrade.toFixed(1) : "—"}</td>
                            <td style={{ ...V.td, color: "#5a6575" }}>{c.avgRank ? c.avgRank.toFixed(2) : "—"}</td>
                            <td style={{ ...V.td, fontSize: 11, color: c.hasTest === "1" ? "#c46b6b" : c.hasTest === "0" ? "#6bc47a" : "#4a5565" }}>
                              {c.hasTest === "1" ? "exam" : c.hasTest === "0" ? "no exam" : "—"}
                            </td>
                            <td style={V.td}>
                              <span
                                style={{ cursor: "pointer", fontSize: 10, letterSpacing: "0.04em", color: isMust ? "#c8b560" : "#4a5565" }}
                                onClick={() => setMustIds(prev => isMust ? prev.filter(x => x !== id) : [...prev, id])}>
                                {isMust ? "✓ must" : "+ must"}
                              </span>
                            </td>
                            <td style={V.td}>
                              <span
                                style={{ cursor: "pointer", fontSize: 10, letterSpacing: "0.04em", color: isBlocked ? "#c46b6b" : "#4a5565" }}
                                onClick={() => setBlockIds(prev => isBlocked ? prev.filter(x => x !== id) : [...prev, id])}>
                                {isBlocked ? "✓ excluded" : "+ exclude"}
                              </span>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                )}
              </div>
            </div>
          </div>
        )}
      </div>
      );
    }

    // ── Step: slider ────────────────────────────────────────────────────────
    if (planStep === "slider") {
      const remaining = (v) => Math.max(0, v - mustPts);
      const effectiveMin = Math.max(mustPts, minPts);
      const effectiveMax = Math.max(mustPts, maxPts);
      return (
        <div style={V.wrap}>
          <style>{css}</style>
          <div style={V.header}>
            <span style={V.logo}>TECHNION <span style={V.logoAccent}>TRACKER</span></span>
            <span style={V.pill}>{semLabel(semInput)}</span>
          </div>
          <div style={V.main}>
            <div className="fade-up">
              <span style={V.back} onClick={() => { setPlanStep("must"); setErr(""); }}>← back</span>
              <div style={V.h1}>Set point range</div>
              <div style={V.sub}>Step 3 of 3 — how many points do you want to take?</div>
              {err && <div style={V.err}>{err}</div>}

              <div style={V.section}>
                {mustPts > 0 && (
                  <div style={{ marginBottom: 20, padding: "10px 14px", background: "#0f0e08", border: "1px solid #3a3010", borderRadius: 6, fontSize: 12, color: "#8a7a30" }}>
                    {mustPts} pts already locked in from required courses · totals below include these
                  </div>
                )}

                <div style={{ display: "flex", alignItems: "flex-end", gap: 20, marginBottom: 16 }}>
                  <div>
                    <div style={{ fontSize: 10, color: "#5a6575", marginBottom: 6, letterSpacing: "0.08em" }}>MIN PTS</div>
                    <input type="number" min={mustPts} step={0.5}
                      value={minPts}
                      onChange={e => setMinPts(e.target.value === "" ? "" : parseFloat(e.target.value))}
                      style={{ ...V.input, width: 110, fontSize: 20, textAlign: "center", color: "#c8b560", fontWeight: 500 }} />
                    {mustPts > 0 && <div style={{ fontSize: 10, color: "#5a6575", marginTop: 6 }}>+{remaining(effectiveMin)} free</div>}
                  </div>
                  <div style={{ fontSize: 18, color: "#4a5565", paddingBottom: 10 }}>–</div>
                  <div>
                    <div style={{ fontSize: 10, color: "#5a6575", marginBottom: 6, letterSpacing: "0.08em" }}>MAX PTS</div>
                    <input type="number" min={mustPts} step={0.5}
                      value={maxPts}
                      onChange={e => setMaxPts(e.target.value === "" ? "" : parseFloat(e.target.value))}
                      style={{ ...V.input, width: 110, fontSize: 20, textAlign: "center", color: "#c8b560", fontWeight: 500 }} />
                    {mustPts > 0 && <div style={{ fontSize: 10, color: "#5a6575", marginTop: 6 }}>+{remaining(effectiveMax)} free</div>}
                  </div>
                </div>

                <div style={{ fontSize: 12, color: "#5a6575", marginBottom: 24 }}>
                  Planning <span style={{ color: "#c8b560" }}>{effectiveMin} – {effectiveMax} pts</span> for {semLabel(semInput)}
                  {mustPts > 0 && <span style={{ color: "#5a5040" }}> ({mustPts} required + {remaining(effectiveMin)}–{remaining(effectiveMax)} elective)</span>}
                </div>

                <div style={{ marginBottom: 24 }}>
                  <div style={{ ...V.secTitle, marginBottom: 10 }}>Exam preference (optional)</div>
                  {EXAM_PREF_CATEGORIES.map(({ cat, label }) => {
                    const val = examPref[cat] || "any";
                    return (
                      <div key={cat} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
                        <span style={{ fontSize: 12, color: "#8a8a95" }} className="he">{label}</span>
                        <div style={{ display: "flex", gap: 4 }}>
                          {[["any", "Any"], ["with", "With exam"], ["without", "No exam"]].map(([v, text]) => (
                            <button key={v}
                              onClick={() => setExamPref(prev => ({ ...prev, [cat]: v }))}
                              style={{
                                fontSize: 10, padding: "4px 10px", borderRadius: 3, cursor: "pointer",
                                border: `1px solid ${val === v ? "#c8b560" : "#1e2530"}`,
                                background: val === v ? "#1a1608" : "transparent",
                                color: val === v ? "#c8b560" : "#5a6575",
                                fontFamily: "'IBM Plex Mono', monospace",
                              }}>
                              {text}
                            </button>
                          ))}
                        </div>
                      </div>
                    );
                  })}
                </div>

                {recError && <div style={{ ...V.err, marginBottom: 16 }}>{recError}</div>}

                {recLoading ? (
                  <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-start", gap: 14 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                      <span className="spin" style={{ fontSize: 20, color: "#c8b560" }}>◐</span>
                      <span style={{ fontSize: 13, color: "#c8b560", letterSpacing: "0.06em" }}>Finding best schedule...</span>
                    </div>
                    <div style={{ display: "flex", gap: 6 }}>
                      {[0,1,2,3,4].map(i => (
                        <div key={i} className="pulse-dot" style={{
                          width: 8, height: 8, borderRadius: "50%", background: "#c8b560",
                          animationDelay: `${i * 0.18}s`
                        }} />
                      ))}
                    </div>
                    <div style={{ fontSize: 11, color: "#5a6575" }}>Checking prerequisites · optimizing combinations</div>
                  </div>
                ) : (
                  <button className="btn-primary" style={V.btnPrimary} onClick={findSchedule}>
                    Find best schedule →
                  </button>
                )}
              </div>
            </div>
          </div>
          {toast && <div className="toast-anim" style={V.toast}>{toast}</div>}
        </div>
      );
    }

    // ── Step: options ──────────────────────────────────────────────────────────────────────────────────────
    if (planStep === "options") {
      const catColor = (cat) => {
        if (!cat) return "#4a5060";
        if (cat.includes("חובה") || cat.includes("מדעי")) return "#c8b560";
        if (cat.includes("נתונים") || cat.includes("עתיר")) return "#6b9bc4";
        if (cat.includes("פקולט")) return "#a06bc4";
        if (cat.includes("ספורט")) return "#6bc47a";
        if (cat.includes("מלג"))   return "#c46b6b";
        return "#4a5060";
      };
      return (
        <div style={V.wrap}>
          <style>{css}</style>
          <div style={V.header}>
            <span style={V.logo}>TECHNION <span style={V.logoAccent}>TRACKER</span></span>
            <span style={V.pill}>{semLabel(semInput)}</span>
          </div>
          <div style={V.main}>
            <div className="fade-up">
              <span style={V.back} onClick={() => setPlanStep("slider")}>back</span>
              <div style={V.h1}>Choose your schedule</div>
              <div style={V.sub}>{recOptions.length} options found · click one to see the full details</div>

              <div style={{ display:"flex", flexDirection:"column", gap:10, marginBottom:28 }}>
                {recOptions.map((opt, i) => (
                  <div key={i} onClick={() => selectOption(opt)}
                    style={{ background:"#0c0e12", border:"1px solid #1e2530", borderRadius:8,
                             padding:"16px 20px", cursor:"pointer", display:"flex", alignItems:"center", gap:0,
                             transition:"border-color .15s" }}
                    onMouseEnter={e => e.currentTarget.style.borderColor="#c8b56044"}
                    onMouseLeave={e => e.currentTarget.style.borderColor="#1e2530"}>

                    {/* Stat pills */}
                    <div style={{ flex:"0 0 72px", textAlign:"center", borderRight:"1px solid #1e2530", paddingRight:16, marginRight:16 }}>
                      <div style={{ fontSize:10, color:"#5a6575", letterSpacing:"0.1em", textTransform:"uppercase", marginBottom:4 }}>pts</div>
                      <div style={{ fontSize:24, color:"#c8b560", fontWeight:600, lineHeight:1 }}>{opt.total_credits}</div>
                    </div>
                    <div style={{ flex:"0 0 60px", textAlign:"center", borderRight:"1px solid #1e2530", paddingRight:16, marginRight:16 }}>
                      <div style={{ fontSize:10, color:"#5a6575", letterSpacing:"0.1em", textTransform:"uppercase", marginBottom:4 }}>courses</div>
                      <div style={{ fontSize:24, color:"#c8b560", fontWeight:600, lineHeight:1 }}>{opt.n_courses}</div>
                    </div>
                    <div style={{ flex:"0 0 72px", textAlign:"center", borderRight:"1px solid #1e2530", paddingRight:16, marginRight:16 }}>
                      <div style={{ fontSize:10, color:"#5a6575", letterSpacing:"0.1em", textTransform:"uppercase", marginBottom:4 }}>avg</div>
                      <div style={{ fontSize:24, fontWeight:600, lineHeight:1,
                                    color: opt.weighted_grade>=85?"#6bc47a":opt.weighted_grade>=70?"#c8b560":"#c46b6b" }}>
                        {opt.weighted_grade > 0 ? opt.weighted_grade.toFixed(1) : "—"}
                      </div>
                    </div>

                    {/* Course tags */}
                    <div style={{ flex:1, display:"flex", flexWrap:"wrap", gap:5 }}>
                      {opt.courses.map(c => (
                        <span key={c.id} className="he" style={{ fontSize:11, padding:"3px 8px", borderRadius:3,
                          background:"#111418", border:`1px solid ${catColor(c.category)}33`,
                          color: c.must ? "#c8b560" : catColor(c.category), whiteSpace:"nowrap" }}>
                          {c.name || c.id}
                        </span>
                      ))}
                    </div>
                    <div style={{ color:"#5a6575", fontSize:14, paddingLeft:12 }}>&#x2192;</div>
                  </div>
                ))}
              </div>
            </div>
          </div>
          {toast && <div className="toast-anim" style={V.toast}>{toast}</div>}
        </div>
      );
    }

        // ── Step: results ───────────────────────────────────────────────────────
    if (planStep === "results" && recommendation) {
      return (
        <div style={V.wrap}>
          <style>{css}</style>
          <div style={V.header}>
            <span style={V.logo}>TECHNION <span style={V.logoAccent}>TRACKER</span></span>
            <span style={V.pill}>{semLabel(semInput)}</span>
          </div>
          <div style={V.main}>
            <div className="fade-up">
              <span style={V.back} onClick={() => { setPlanStep("slider"); setRecError(""); }}>← back</span>
              <div style={V.h1}>Recommended schedule</div>
              <div style={V.sub}>{semLabel(semInput)}</div>
              <div style={{ display: "flex", gap: 20, marginBottom: 28, flexWrap: "wrap" }}>
                <div style={{ background: "#0c0e12", border: "1px solid #181c24", borderRadius: 8, padding: "14px 24px", textAlign: "center" }}>
                  <div style={{ fontSize: 10, letterSpacing: "0.12em", textTransform: "uppercase", color: "#5a6575", marginBottom: 6 }}>Total credits</div>
                  <div style={{ fontSize: 28, color: "#c8b560", fontWeight: 500 }}>{recommendation.total_credits}</div>
                </div>
                <div style={{ background: "#0c0e12", border: "1px solid #181c24", borderRadius: 8, padding: "14px 24px", textAlign: "center" }}>
                  <div style={{ fontSize: 10, letterSpacing: "0.12em", textTransform: "uppercase", color: "#5a6575", marginBottom: 6 }}>Est. weighted avg</div>
                  <div style={{ fontSize: 28, fontWeight: 500, color: recommendation.weighted_grade >= 85 ? "#6bc47a" : recommendation.weighted_grade >= 70 ? "#c8b560" : "#c46b6b" }}>
                    {recommendation.weighted_grade > 0 ? recommendation.weighted_grade.toFixed(1) : "—"}
                  </div>
                </div>
                <div style={{ background: "#0c0e12", border: "1px solid #181c24", borderRadius: 8, padding: "14px 24px", textAlign: "center" }}>
                  <div style={{ fontSize: 10, letterSpacing: "0.12em", textTransform: "uppercase", color: "#5a6575", marginBottom: 6 }}>Courses</div>
                  <div style={{ fontSize: 28, color: "#c8b560", fontWeight: 500 }}>{recommendation.courses.length}</div>
                </div>
              </div>

              <div style={V.section}>
                <table style={V.table}>
                  <thead><tr>
                    <th style={V.th}>Course ID</th>
                    <th style={V.th}>Name</th>
                    <th style={V.th}>Category</th>
                    <th style={V.th}>Credits</th>
                    <th style={V.th}>Avg grade</th>
                    <th style={V.th}>Exam</th>
                    <th style={V.th}></th>
                  </tr></thead>
                  <tbody>
                    {recommendation.courses.map(c => (
                      <tr key={c.id} className="row-hover">
                        <td style={{ ...V.td, color: "#c8b560", letterSpacing: "0.05em" }}>{c.id}</td>
                        <td style={{ ...V.td, color: "#9a9090" }} className="he">{c.name}</td>
                        <td style={{ ...V.td }}>
                          <span style={{ color: catColor(c.category), fontSize: 11 }} className="he">{c.category}</span>
                        </td>
                        <td style={{ ...V.td, color: "#4a5060" }}>{c.credits}</td>
                        <td style={{ ...V.td, color: c.grade ? "#6bc47a" : "#5a6575" }}>
                          {c.grade ? c.grade.toFixed(1) : "—"}
                        </td>
                        <td style={{ ...V.td, fontSize: 11, color: c.has_test === "1" ? "#c46b6b" : c.has_test === "0" ? "#6bc47a" : "#4a5565" }}>
                          {c.has_test === "1" ? "exam" : c.has_test === "0" ? "no exam" : "—"}
                        </td>
                        <td style={{ ...V.td }}>
                          {c.must && <span style={{ fontSize: 10, color: "#c8b560", letterSpacing: "0.06em" }}>REQUIRED</span>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {recommendation.statusBefore && recommendation.statusAfter && (() => {
                const req = recommendation.statusAfter?.requirements || {};
                const before = recommendation.statusBefore?.missing || {};
                const after  = recommendation.statusAfter?.missing || {};
                const rows = Object.keys(REQ_LABELS).filter(k => (before[k] || 0) > 0 || (after[k] || 0) > 0);
                return (
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, marginBottom: 28 }}>
                    <div style={V.section}>
                      <div style={{ ...V.secTitle, marginBottom: 14 }}>Currently missing</div>
                      {rows.map(k => (
                        <div key={k} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
                          <span style={{ fontSize: 12, color: "#6a7080" }} className="he">{REQ_LABELS[k]}</span>
                          <span style={{ fontSize: 13, color: before[k] > 0 ? "#c46b6b" : "#6bc47a", fontWeight: 600 }}>
                            {before[k] > 0 ? `-${before[k]}` : "✓"}
                          </span>
                        </div>
                      ))}
                    </div>
                    <div style={V.section}>
                      <div style={{ ...V.secTitle, marginBottom: 14 }}>After this semester</div>
                      {rows.map(k => {
                        const diff = (before[k] || 0) - (after[k] || 0);
                        return (
                          <div key={k} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
                            <span style={{ fontSize: 12, color: "#6a7080" }} className="he">{REQ_LABELS[k]}</span>
                            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                              {diff > 0 && <span style={{ fontSize: 10, color: "#6bc47a" }}>+{diff}</span>}
                              <span style={{ fontSize: 13, color: after[k] > 0 ? "#c46b6b" : "#6bc47a", fontWeight: 600 }}>
                                {after[k] > 0 ? `-${after[k]}` : "✓"}
                              </span>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                );
              })()}

              <div style={{ display: "flex", gap: 12 }}>
                <button className="btn-primary" style={V.btnPrimary} onClick={() => setPlanStep("options")}>
                  ← Choose different
                </button>
                <button className="btn-ghost" style={V.btnGhost} onClick={goBack}>Start over</button>
              </div>
            </div>
          </div>
          {toast && <div className="toast-anim" style={V.toast}>{toast}</div>}
        </div>
      );
    }

    return null;
  }

    // ─── HOME view ────────────────────────────────────────────────────────────
  return (
    <div style={V.wrap}>
      <style>{css}</style>
      <div style={V.header}>
        <span style={V.logo}>TECHNION <span style={V.logoAccent}>TRACKER</span></span>
        <span style={V.pill}>{takenCount} courses saved</span>
      </div>
      <div style={V.main}>
        <div className="fade-up">
          <div style={V.h1}>Course tracker</div>
          <div style={V.sub}>
            {takenCount === 0
              ? "Start by uploading your transcript to import your courses."
              : `${takenCount} courses loaded · manage your progress below.`}
          </div>

          {dbLoading && (
            <div style={{ ...V.section, borderColor: "#1e2530", marginBottom: 20 }}>
              <div style={{ fontSize: 12, color: "#5a6575" }}>Loading course database...</div>
            </div>
          )}
          {!dbLoading && Object.keys(coursesDb).length === 0 && (
            <div style={{ ...V.section, borderColor: "#3a3010", background: "#0f0e08", marginBottom: 20 }}>
              <div style={{ fontSize: 12, color: "#8a7a30" }}>
                ⚠ Could not load course database — make sure <span style={{ color: "#c8b560" }}>courses_labeled.csv</span> is in <span style={{ color: "#c8b560" }}>ui/public/</span>
              </div>
            </div>
          )}
          {!dbLoading && Object.keys(coursesDb).length > 0 && (
            <div style={{ fontSize: 11, color: "#3a5030", marginBottom: 20, letterSpacing: "0.06em" }}>
              ✓ {Object.keys(coursesDb).length} courses loaded
            </div>
          )}
          <div style={V.grid2}>
            <div className="card-hover" style={V.card} onClick={() => { setErr(""); setView("upload"); }}>
              <div style={V.cardLabel}>↑ Upload transcript</div>
              <div style={V.cardDesc}>Import your Technion תדפיס ציונים PDF. Automatically detects passed and failed courses.</div>
            </div>
            <div className="card-hover" style={V.card} onClick={() => { setErr(""); setView("manual"); }}>
              <div style={V.cardLabel}>+ Add courses manually</div>
              <div style={V.cardDesc}>Add courses not on your transcript — exemptions, transfers, or courses in progress.</div>
            </div>
            <div className="card-hover" style={V.card} onClick={() => { setErr(""); setView("plan"); }}>
              <div style={V.cardLabel}>→ Plan next semester</div>
              <div style={V.cardDesc}>Enter a semester code and get course recommendations based on your progress.</div>
            </div>
          </div>

          {takenCount > 0 && (
            <div style={V.section}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 14 }}>
                <div style={V.secTitle}>Saved courses</div>
                <input style={{ ...V.input, width: 180, padding: "6px 10px" }}
                  placeholder="Search…" value={search} onChange={(e) => setSearch(e.target.value)} />
              </div>
              <table style={V.table}>
                <thead><tr>
                  <th style={V.th}>Course ID</th>
                  <th style={V.th}>Name</th>
                  <th style={V.th}>Grade</th>
                  <th style={V.th}>Source</th>
                  <th style={V.th}></th>
                </tr></thead>
                <tbody>
                  {(search ? filtered : takenList).slice(0, 20).map(([cid, info]) => (
                    <tr key={cid} className="row-hover">
                      <td style={{ ...V.td, color: "#c8b560", letterSpacing: "0.05em" }}>{cid}</td>
                      <td style={{ ...V.td, color: "#6a7080", maxWidth: 280, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{info.name}</td>
                      <td style={V.td}><GradePill g={info.grade} /></td>
                      <td style={V.td}><SourceDot s={info.source} /></td>
                      <td style={{ ...V.td, textAlign: "right" }}>
                        <span className="remove-btn" style={{ color: "#3a2020", cursor: "pointer", fontSize: 11 }}
                          onClick={() => removeCourse(cid)}>✕</span>
                      </td>
                    </tr>
                  ))}
                  {!search && takenCount > 20 && (
                    <tr>
                      <td colSpan={5} style={{ ...V.td, color: "#4a5565", textAlign: "center", padding: 16, cursor: "pointer" }}
                        onClick={() => { setSearch(""); setView("manual"); }}>
                        + {takenCount - 20} more — view all →
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
      {toast && <div className="toast-anim" style={V.toast}>{toast}</div>}
    </div>
  );
}