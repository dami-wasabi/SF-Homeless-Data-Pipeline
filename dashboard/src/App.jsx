// dashboard/src/App.jsx
// ─────────────────────────────────────────────────────────────────────────────
// SF Homeless Pilot – React dashboard
//
// Displays:
//   • KPI summary cards  (total encounters, unique individuals, avg anxiety)
//   • Anxiety trend over time (line chart)
//   • Average anxiety by shelter (bar chart)
//   • Full encounter table with search + sort
//
// Data source: API Gateway endpoint set via REACT_APP_API_URL env variable.
// ─────────────────────────────────────────────────────────────────────────────

import { useState, useEffect, useCallback } from "react";
import {
  LineChart, Line, BarChart, Bar,
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell,
} from "recharts";

const API_BASE = import.meta.env.VITE_API_URL || "http://localhost:3001";

// ── Colour palette ────────────────────────────────────────────────────────────
const TEAL   = "#1D9E75";
const AMBER  = "#BA7517";
const CORAL  = "#D85A30";
const PURPLE = "#7F77DD";
const SHELTER_COLORS = [TEAL, AMBER, CORAL, PURPLE, "#378ADD", "#639922"];

// ─────────────────────────────────────────────────────────────────────────────
// Fetch helpers
// ─────────────────────────────────────────────────────────────────────────────
async function apiFetch(path) {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) throw new Error(`API ${res.status}: ${res.statusText}`);
  return res.json();
}

// ─────────────────────────────────────────────────────────────────────────────
// Sub-components
// ─────────────────────────────────────────────────────────────────────────────

function KpiCard({ label, value, sub, color }) {
  return (
    <div style={{
      background: "var(--card-bg, #fff)",
      border: "1px solid #e5e3db",
      borderRadius: 12,
      padding: "20px 24px",
      flex: "1 1 180px",
      minWidth: 160,
    }}>
      <p style={{ margin: 0, fontSize: 12, color: "#888", fontFamily: "monospace", letterSpacing: 1, textTransform: "uppercase" }}>{label}</p>
      <p style={{ margin: "8px 0 4px", fontSize: 32, fontWeight: 600, color: color || "#1a1a1a", fontFamily: "'Georgia', serif" }}>{value ?? "—"}</p>
      {sub && <p style={{ margin: 0, fontSize: 12, color: "#aaa" }}>{sub}</p>}
    </div>
  );
}

function SectionTitle({ children }) {
  return (
    <h2 style={{
      fontSize: 13, fontFamily: "monospace", letterSpacing: 2,
      textTransform: "uppercase", color: "#555", margin: "32px 0 16px",
      borderBottom: "1px solid #e5e3db", paddingBottom: 8,
    }}>{children}</h2>
  );
}

function LoadingBar() {
  return (
    <div style={{ height: 4, background: "#e5e3db", borderRadius: 2, overflow: "hidden", margin: "8px 0 24px" }}>
      <div style={{
        height: "100%", width: "40%", background: TEAL, borderRadius: 2,
        animation: "slide 1.2s ease-in-out infinite",
      }} />
      <style>{`@keyframes slide { 0%{transform:translateX(-100%)} 100%{transform:translateX(350%)} }`}</style>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Main App
// ─────────────────────────────────────────────────────────────────────────────
export default function App() {
  const [summary, setSummary]       = useState(null);
  const [encounters, setEncounters] = useState([]);
  const [loading, setLoading]       = useState(true);
  const [error, setError]           = useState(null);
  const [search, setSearch]         = useState("");
  const [sortKey, setSortKey]       = useState("encounter_date");
  const [sortDir, setSortDir]       = useState("desc");
  const [shelterFilter, setShelterFilter] = useState("All");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [sum, enc] = await Promise.all([
        apiFetch("/summary"),
        apiFetch("/encounters?limit=500"),
      ]);
      setSummary(sum);
      setEncounters(enc.encounters ?? []);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  // ── Table helpers ──────────────────────────────────────────────────────────
  const shelters = ["All", ...new Set(encounters.map(e => e.shelter).filter(Boolean))].sort();

  const filtered = encounters
    .filter(e => shelterFilter === "All" || e.shelter === shelterFilter)
    .filter(e => {
      if (!search) return true;
      const q = search.toLowerCase();
      return (
        (e.hid || "").toLowerCase().includes(q) ||
        (e.shelter || "").toLowerCase().includes(q) ||
        (e.gender || "").toLowerCase().includes(q) ||
        (e.race || "").toLowerCase().includes(q)
      );
    })
    .sort((a, b) => {
      const av = a[sortKey] ?? "";
      const bv = b[sortKey] ?? "";
      const cmp = av < bv ? -1 : av > bv ? 1 : 0;
      return sortDir === "asc" ? cmp : -cmp;
    });

  const toggleSort = (key) => {
    if (sortKey === key) setSortDir(d => d === "asc" ? "desc" : "asc");
    else { setSortKey(key); setSortDir("asc"); }
  };

  const SortIcon = ({ k }) => sortKey !== k ? null : (
    <span style={{ marginLeft: 4, opacity: 0.6 }}>{sortDir === "asc" ? "↑" : "↓"}</span>
  );

  // ─────────────────────────────────────────────────────────────────────────
  return (
    <div style={{
      fontFamily: "'Helvetica Neue', Arial, sans-serif",
      background: "#f8f7f4",
      minHeight: "100vh",
      color: "#1a1a1a",
    }}>
      {/* Header */}
      <div style={{ background: "#1a1a1a", padding: "20px 40px", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div>
          <p style={{ margin: 0, fontSize: 11, color: "#888", fontFamily: "monospace", letterSpacing: 2, textTransform: "uppercase" }}>Element 84 · SF Homeless Pilot</p>
          <h1 style={{ margin: "4px 0 0", fontSize: 22, color: "#fff", fontWeight: 400, fontFamily: "'Georgia', serif" }}>Shelter Anxiety Dashboard</h1>
        </div>
        <button
          onClick={load}
          disabled={loading}
          style={{
            background: "transparent", border: "1px solid #555", color: "#ccc",
            padding: "8px 16px", borderRadius: 6, cursor: "pointer", fontSize: 12,
            fontFamily: "monospace", letterSpacing: 1,
          }}
        >{loading ? "Loading…" : "↻ Refresh"}</button>
      </div>

      <div style={{ padding: "32px 40px", maxWidth: 1200, margin: "0 auto" }}>

        {error && (
          <div style={{ background: "#fff0ee", border: "1px solid #f09595", borderRadius: 8, padding: "12px 16px", marginBottom: 24, color: "#a32d2d", fontSize: 13 }}>
            ⚠ {error} — check that <code>REACT_APP_API_URL</code> is set correctly.
          </div>
        )}

        {loading && <LoadingBar />}

        {/* KPI Cards */}
        {summary && (
          <>
            <SectionTitle>Overview</SectionTitle>
            <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
              <KpiCard label="Total encounters"   value={summary.total_encounters}   color={TEAL} />
              <KpiCard label="Unique individuals" value={summary.unique_individuals} color={PURPLE} />
              <KpiCard label="Avg anxiety level"  value={summary.avg_anxiety != null ? summary.avg_anxiety.toFixed(1) : null} sub="Scale 0 – 10" color={CORAL} />
              <KpiCard label="Shelters tracked"   value={summary.shelters?.length}   color={AMBER} />
            </div>
          </>
        )}

        {/* Charts */}
        {summary?.anxiety_over_time?.length > 0 && (
          <>
            <SectionTitle>Anxiety level over time</SectionTitle>
            <div style={{ background: "#fff", border: "1px solid #e5e3db", borderRadius: 12, padding: "24px 16px 8px" }}>
              <ResponsiveContainer width="100%" height={240}>
                <LineChart data={summary.anxiety_over_time} margin={{ left: 0, right: 16 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#f0ede8" />
                  <XAxis dataKey="month" tick={{ fontSize: 11, fill: "#999" }} />
                  <YAxis domain={[0, 10]} tick={{ fontSize: 11, fill: "#999" }} />
                  <Tooltip
                    formatter={(v) => [v.toFixed(2), "Avg anxiety"]}
                    contentStyle={{ fontSize: 12, borderRadius: 6, border: "1px solid #e5e3db" }}
                  />
                  <Line type="monotone" dataKey="avg_anxiety" stroke={TEAL} strokeWidth={2} dot={{ r: 4, fill: TEAL }} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </>
        )}

        {summary?.by_shelter?.length > 0 && (
          <>
            <SectionTitle>Average anxiety by shelter</SectionTitle>
            <div style={{ background: "#fff", border: "1px solid #e5e3db", borderRadius: 12, padding: "24px 16px 8px" }}>
              <ResponsiveContainer width="100%" height={220}>
                <BarChart data={summary.by_shelter} margin={{ left: 0, right: 16 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#f0ede8" />
                  <XAxis dataKey="shelter" tick={{ fontSize: 10, fill: "#999" }} />
                  <YAxis domain={[0, 10]} tick={{ fontSize: 11, fill: "#999" }} />
                  <Tooltip
                    formatter={(v, n) => [v.toFixed(2), n === "avg_anxiety" ? "Avg anxiety" : "Encounters"]}
                    contentStyle={{ fontSize: 12, borderRadius: 6, border: "1px solid #e5e3db" }}
                  />
                  <Bar dataKey="avg_anxiety" radius={[4, 4, 0, 0]}>
                    {summary.by_shelter.map((_, i) => (
                      <Cell key={i} fill={SHELTER_COLORS[i % SHELTER_COLORS.length]} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          </>
        )}

        {/* Encounter table */}
        <SectionTitle>All encounters</SectionTitle>
        <div style={{ display: "flex", gap: 12, marginBottom: 16, flexWrap: "wrap" }}>
          <input
            placeholder="Search HID, shelter, gender, race…"
            value={search}
            onChange={e => setSearch(e.target.value)}
            style={{
              flex: "1 1 240px", padding: "8px 12px", borderRadius: 6,
              border: "1px solid #ddd", fontSize: 13, outline: "none",
            }}
          />
          <select
            value={shelterFilter}
            onChange={e => setShelterFilter(e.target.value)}
            style={{ padding: "8px 12px", borderRadius: 6, border: "1px solid #ddd", fontSize: 13, background: "#fff" }}
          >
            {shelters.map(s => <option key={s}>{s}</option>)}
          </select>
        </div>

        <div style={{ background: "#fff", border: "1px solid #e5e3db", borderRadius: 12, overflow: "hidden" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
            <thead>
              <tr style={{ background: "#f8f7f4", borderBottom: "1px solid #e5e3db" }}>
                {[
                  ["hid",            "HID"],
                  ["shelter",        "Shelter"],
                  ["encounter_date", "Encounter date"],
                  ["anxiety_level",  "Anxiety level"],
                  ["gender",         "Gender"],
                  ["race",           "Race"],
                ].map(([key, label]) => (
                  <th
                    key={key}
                    onClick={() => toggleSort(key)}
                    style={{ padding: "10px 14px", textAlign: "left", cursor: "pointer", fontWeight: 500, userSelect: "none", whiteSpace: "nowrap" }}
                  >{label}<SortIcon k={key} /></th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filtered.length === 0 ? (
                <tr><td colSpan={6} style={{ padding: "32px 14px", textAlign: "center", color: "#aaa" }}>No records match your filter.</td></tr>
              ) : filtered.map((enc, i) => (
                <tr key={i} style={{ borderBottom: "1px solid #f0ede8", background: i % 2 ? "#faf9f7" : "#fff" }}>
                  <td style={{ padding: "9px 14px", fontFamily: "monospace", fontSize: 12, color: TEAL }}>{enc.hid}</td>
                  <td style={{ padding: "9px 14px" }}>{enc.shelter}</td>
                  <td style={{ padding: "9px 14px", color: "#666" }}>{enc.encounter_date}</td>
                  <td style={{ padding: "9px 14px" }}>
                    <span style={{
                      display: "inline-block", minWidth: 28, textAlign: "center",
                      padding: "2px 8px", borderRadius: 20, fontWeight: 600, fontSize: 12,
                      background: enc.anxiety_level >= 7 ? "#fff0ee" : enc.anxiety_level >= 4 ? "#faeeda" : "#eaf3de",
                      color:      enc.anxiety_level >= 7 ? CORAL      : enc.anxiety_level >= 4 ? AMBER       : TEAL,
                    }}>{enc.anxiety_level ?? "—"}</span>
                  </td>
                  <td style={{ padding: "9px 14px", color: "#666" }}>{enc.gender}</td>
                  <td style={{ padding: "9px 14px", color: "#666" }}>{enc.race}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div style={{ padding: "10px 14px", borderTop: "1px solid #f0ede8", fontSize: 12, color: "#aaa" }}>
            Showing {filtered.length} of {encounters.length} encounters
          </div>
        </div>
      </div>
    </div>
  );
}
