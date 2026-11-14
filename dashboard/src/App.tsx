import { marked } from "marked";
import { useEffect, useMemo, useState } from "react";
import { api, type Finding, type ReportRef, type ReviewDetail, type ReviewRow, type Summary } from "./api";

const pct = (x: number | null) => (x === null ? "–" : `${Math.round(x * 100)}%`);
const usd = (x: number) => (x === 0 ? "$0" : x < 0.01 ? "<$0.01" : `$${x.toFixed(2)}`);
const secs = (s: number | null) =>
  s === null ? "–" : s < 60 ? `${s.toFixed(0)} s` : `${Math.floor(s / 60)} min ${Math.round(s % 60)} s`;

function ago(iso: string | null): string {
  if (!iso) return "–";
  const m = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  return h < 48 ? `${h} h ago` : `${Math.round(h / 24)} days ago`;
}

function Risk({ row }: { row: Pick<ReviewRow, "status" | "verdict"> }) {
  if (row.status !== "complete") return <span className="chip wait">awaiting verification</span>;
  return <span className={`chip ${row.verdict ?? "wait"}`}>{row.verdict ?? "–"}</span>;
}

function Figures({ s }: { s: Summary }) {
  const items = [
    { label: "Reviews", value: String(s.reviews), sub: `${s.on_github} posted on GitHub` },
    {
      label: "Comments posted",
      value: String(s.comments_posted),
      sub: `of ${s.comments_considered} considered (${pct(s.kept_rate)})`,
    },
    {
      label: "Accept rate",
      value: pct(s.accept_rate),
      sub: s.rated ? `${s.rated} rated by reactions` : "no reactions yet",
    },
    { label: "Median review time", value: secs(s.median_duration_s), sub: "last run of each review" },
    { label: "Model cost", value: usd(s.cost_usd), sub: "API calls; handoff is free" },
  ];
  return (
    <div className="figures">
      {items.map((f) => (
        <div className="figure" key={f.label}>
          <div className="label">{f.label}</div>
          <div className="value num">{f.value}</div>
          <div className="sub">{f.sub}</div>
        </div>
      ))}
    </div>
  );
}

type Tip = { x: number; y: number; text: string } | null;

function Bars({ title, rows, unit }: { title: string; rows: [string, number][]; unit: string }) {
  const [tip, setTip] = useState<Tip>(null);
  const max = Math.max(1, ...rows.map(([, v]) => v));
  const total = rows.reduce((a, [, v]) => a + v, 0);
  return (
    <figure className="chart" style={{ margin: 0 }}>
      <h3>{title}</h3>
      <div className="bars" role="list">
        {rows.map(([name, v]) => (
          <div
            className="bar-row"
            role="listitem"
            key={name}
            aria-label={`${name}: ${v} ${unit}`}
            onMouseMove={(e) =>
              setTip({
                x: e.clientX + 12,
                y: e.clientY + 12,
                text: `${name}: ${v} ${unit}${total ? ` (${Math.round((v / total) * 100)}%)` : ""}`,
              })
            }
            onMouseLeave={() => setTip(null)}
          >
            <span className="name">{name}</span>
            <span className="bar-track">
              {v > 0 && (
                <span
                  className="bar"
                  style={{ display: "block", width: `max(3px, ${(v / max) * 100}%)` }}
                />
              )}
            </span>
            <span className="count num">{v}</span>
          </div>
        ))}
      </div>
      {tip && (
        <div className="tip" style={{ left: tip.x, top: tip.y }}>
          {tip.text}
        </div>
      )}
    </figure>
  );
}

function FindingRow({ f }: { f: Finding }) {
  const fb = f.feedback;
  return (
    <div className={`finding ${f.posted ? "" : "dropped"}`}>
      <div className="where mono">
        {f.path.split("/").pop()}:{f.line}
      </div>
      <div>
        <div className="text">{f.comment}</div>
        {f.suggested_fix && <div className="fix">Suggested fix: {f.suggested_fix}</div>}
        {f.verifier_note && <div className="why">Verifier: {f.verifier_note}</div>}
      </div>
      <div className="side">
        <div>
          {f.group}
          {f.author.toLowerCase() !== f.group.toLowerCase() && ` · ${f.author}`}
        </div>
        <div>
          {f.posted ? (
            <>
              {f.severity} {f.category} · {f.confidence?.toFixed(2)}
            </>
          ) : (
            <span className="muted">dropped: {f.decision}</span>
          )}
        </div>
        {fb && (
          <div className="muted">
            {fb.thumbs_up} up · {fb.thumbs_down} down
            {fb.replies ? ` · ${fb.replies} repl${fb.replies > 1 ? "ies" : "y"}` : ""}
            {fb.resolved ? " · resolved" : ""}
          </div>
        )}
      </div>
    </div>
  );
}

function Detail({ id }: { id: number }) {
  const [r, setR] = useState<ReviewDetail | null>(null);
  useEffect(() => {
    setR(null);
    api.review(id).then(setR).catch(() => setR(null));
  }, [id]);
  if (!r) return <div className="detail muted">Loading…</div>;
  const posted = r.findings.filter((f) => f.posted);
  const dropped = r.findings.filter((f) => !f.posted);
  return (
    <section className="detail" aria-label="Review detail">
      <div className="detail-head">
        <h2>
          {r.repo}
          {r.pr_number !== null && ` #${r.pr_number}`}
        </h2>
        <span className="mono muted">{r.head_sha.slice(0, 7)}</span>
        <Risk row={r} />
        {r.pr_url && (
          <a href={r.pr_url} target="_blank" rel="noreferrer">
            Open on GitHub
          </a>
        )}
      </div>
      <p className="summary-text">{r.summary}</p>
      {posted.map((f) => (
        <FindingRow key={f.id} f={f} />
      ))}
      {dropped.length > 0 && (
        <details>
          <summary className="muted" style={{ cursor: "pointer", padding: "10px 0" }}>
            {dropped.length} dropped by the verifier or the checks
          </summary>
          {dropped.map((f) => (
            <FindingRow key={f.id} f={f} />
          ))}
        </details>
      )}
    </section>
  );
}

function Reviews() {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [rows, setRows] = useState<ReviewRow[] | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([api.summary(), api.reviews()])
      .then(([s, r]) => {
        setSummary(s);
        setRows(r);
      })
      .catch((e: Error) => setError(e.message));
  }, []);

  if (error) return <p className="empty">Could not load reviews ({error}). Is `prism dashboard` running?</p>;
  if (!summary || !rows) return <p className="empty">Loading…</p>;

  const authorRows: [string, number][] = Object.entries(summary.authors);
  const routeRows: [string, number][] = [
    ["local model", summary.routing.local],
    ["Claude", summary.routing.claude],
  ];

  return (
    <>
      <Figures s={summary} />
      <div className="charts">
        <Bars title="Who wrote the posted comments" rows={authorRows} unit="comments" />
        <Bars title="Where reviewed hunks went" rows={routeRows} unit="hunks" />
      </div>
      <section className="section">
        <div className="section-head">
          <h2>Reviews</h2>
          <span className="muted" style={{ fontSize: 12 }}>
            newest first · select a row for its comments
          </span>
        </div>
        {rows.length === 0 ? (
          <p className="empty">No reviews stored yet.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Pull request</th>
                <th className="hide-sm">Commit</th>
                <th>Risk</th>
                <th className="r">Comments</th>
                <th className="r hide-sm">Feedback</th>
                <th className="r hide-sm">Time</th>
                <th className="r hide-sm">Cost</th>
                <th className="r">When</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <ReviewLine key={r.id} r={r} open={open === r.id} onOpen={() => setOpen(open === r.id ? null : r.id)} />
              ))}
            </tbody>
          </table>
        )}
      </section>
      {open !== null && <Detail id={open} />}
    </>
  );
}

function ReviewLine({ r, open, onOpen }: { r: ReviewRow; open: boolean; onOpen: () => void }) {
  const [owner, name] = r.repo.includes("/") ? r.repo.split("/", 2) : ["", r.repo];
  return (
    <tr
      aria-selected={open}
      tabIndex={0}
      onClick={onOpen}
      onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), onOpen())}
    >
      <td>
        {owner && <span className="repo hide-sm">{owner}/</span>}
        {name}
        {r.pr_number !== null ? <span className="muted"> #{r.pr_number}</span> : <span className="muted"> local</span>}
      </td>
      <td className="mono muted hide-sm">{r.head_sha.slice(0, 7)}</td>
      <td>
        <Risk row={r} />
      </td>
      <td className="r num">
        {r.posted}
        <span className="muted"> / {r.considered}</span>
      </td>
      <td className="r num hide-sm">{r.good + r.bad ? `${r.good} up · ${r.bad} down` : <span className="muted">–</span>}</td>
      <td className="r num hide-sm">{secs(r.duration_s || null)}</td>
      <td className="r num hide-sm">{usd(r.cost_usd)}</td>
      <td className="r muted">{ago(r.posted_at ?? r.created_at)}</td>
    </tr>
  );
}

function Reports() {
  const [list, setList] = useState<ReportRef[]>([]);
  const [current, setCurrent] = useState<string | null>(null);
  const [text, setText] = useState<string>("");
  useEffect(() => {
    api.reports().then((r) => {
      setList(r);
      if (r.length) setCurrent(r.find((x) => x.name === "review_pipeline")?.name ?? r[0].name);
    });
  }, []);
  useEffect(() => {
    if (current) api.report(current).then(setText);
  }, [current]);
  // The reports are files from this repository, rendered as written.
  const html = useMemo(() => marked.parse(text, { async: false }), [text]);
  if (!list.length) return <p className="empty">Loading…</p>;
  return (
    <div className="reports">
      <nav className="report-list" aria-label="Eval reports">
        {list.map((r) => (
          <button key={r.name} aria-selected={r.name === current} onClick={() => setCurrent(r.name)}>
            {r.title}
          </button>
        ))}
      </nav>
      <article className="prose" dangerouslySetInnerHTML={{ __html: html }} />
    </div>
  );
}

export default function App() {
  const [tab, setTab] = useState<"reviews" | "reports">("reviews");
  return (
    <div className="shell">
      <header className="top">
        <div className="mark">
          PR<span>ism</span>
        </div>
        <nav className="tabs" role="tablist">
          <button className="tab" role="tab" aria-selected={tab === "reviews"} onClick={() => setTab("reviews")}>
            Reviews
          </button>
          <button className="tab" role="tab" aria-selected={tab === "reports"} onClick={() => setTab("reports")}>
            Eval reports
          </button>
        </nav>
        <div className="meta">local · read only</div>
      </header>
      {tab === "reviews" ? <Reviews /> : <Reports />}
    </div>
  );
}
