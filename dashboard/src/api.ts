// Types and calls for the API in prism/dashboard.py.

export type Routing = { local: number; claude: number };

export type ReviewRow = {
  id: number;
  repo: string;
  pr_number: number | null;
  pr_url: string | null;
  head_sha: string;
  status: "complete" | "awaiting_verification";
  verdict: "low" | "medium" | "high" | null;
  created_at: string;
  posted_at: string | null;
  on_github: boolean;
  posted: number;
  considered: number;
  good: number;
  bad: number;
  cost_usd: number;
  duration_s: number;
  routing: Routing;
  authors: Record<string, number>;
};

export type Feedback = {
  thumbs_up: number;
  thumbs_down: number;
  replies: number;
  resolved: boolean;
  label: string;
};

export type Finding = {
  id: number;
  path: string;
  line: number;
  author: string;
  group: string;
  comment: string;
  posted: boolean;
  decision: string;
  verifier_note: string;
  severity: string | null;
  category: string | null;
  confidence: number | null;
  suggested_fix: string | null;
  feedback: Feedback | null;
};

export type ReviewDetail = ReviewRow & { summary: string; findings: Finding[] };

export type Summary = {
  reviews: number;
  complete: number;
  awaiting_verification: number;
  on_github: number;
  comments_posted: number;
  comments_considered: number;
  kept_rate: number | null;
  rated: number;
  accept_rate: number | null;
  cost_usd: number;
  median_duration_s: number | null;
  authors: Record<string, number>;
  routing: Routing;
};

export type ReportRef = { name: string; title: string };

async function get<T>(path: string): Promise<T> {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`${path}: ${res.status}`);
  return (await res.json()) as T;
}

export const api = {
  summary: () => get<Summary>("/api/summary"),
  reviews: () => get<ReviewRow[]>("/api/reviews"),
  review: (id: number) => get<ReviewDetail>(`/api/reviews/${id}`),
  reports: () => get<ReportRef[]>("/api/reports"),
  report: async (name: string) => {
    const res = await fetch(`/api/reports/${encodeURIComponent(name)}`);
    if (!res.ok) throw new Error(`report ${name}: ${res.status}`);
    return res.text();
  },
};
