import type { CompareSide, Meta, Plan, SavedBriefing, Settings, SourceCard, TopicDetail } from "./types";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function json<T>(resp: Response): Promise<T> {
  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      detail = (await resp.json()).detail ?? detail;
    } catch {
      /* not JSON */
    }
    throw new ApiError(resp.status, detail);
  }
  return resp.json() as Promise<T>;
}

const post = (url: string, body: unknown, signal?: AbortSignal) =>
  fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify(body),
    signal,
  });

export const api = {
  me: () =>
    fetch("/api/me", { credentials: "same-origin" }).then((r) =>
      json<{ email: string | null; is_admin: boolean; auth_required: boolean }>(r),
    ),
  login: (email: string, password: string) => post("/api/auth/login", { email, password }).then(json),
  logout: () => post("/api/auth/logout", {}).then(json),
  meta: () => fetch("/api/meta", { credentials: "same-origin" }).then((r) => json<Meta>(r)),
  briefing: (id: string) => fetch(`/api/briefings/${id}`).then((r) => json<SavedBriefing>(r)),
  topic: (slug: string) => fetch(`/api/topics/${slug}`).then((r) => json<TopicDetail>(r)),
  feedback: (question: string, useful: boolean, briefingKey?: string) =>
    post("/api/feedback", { question, useful, briefing_key: briefingKey ?? null }).then(json),
  report: (body: ReportInput) => post("/api/report", body).then(json),
  /** Cookie-free page count: the path and the referring site only. */
  hit: (path: string, referrer: string) =>
    post("/api/hit", { path, referrer: referrer || null }).catch(() => undefined),
  /** something done that shows the site working (opening a source), counted once a day per visitor, cookie-free */
  event: (event: "source") =>
    post("/api/hit", { path: window.location.pathname, event }).catch(() => undefined),
  search: (question: string, settings: Settings, plan?: Plan) =>
    post("/api/search", askBody(question, settings, plan)).then((r) =>
      json<{ plan: Plan; sources: SourceCard[] }>(r),
    ),
};

export interface ReportInput {
  kind: "correction" | "complaint" | "general";
  message: string;
  briefing_id?: string | null;
  contact?: string | null;
  website?: string; // must stay empty: a trap for spam bots
}

export function askBody(
  question: string,
  settings: Settings,
  plan?: Plan,
  history: { question: string; answer?: string; topic?: string; scope?: string }[] = [],
  sourcesOnly = false,
  compare: CompareSide[] = [],
  follows?: string,
) {
  return {
    question,
    compare: plan ? [] : compare.map(({ kind, id }) => ({ kind, id })),
    sources_only: sourcesOnly,
    mode: settings.mode,
    recency: settings.recency,
    role_weight: settings.roleWeight,
    scope: settings.scope,
    plan: plan
      ? {
          search_text: plan.search_text,
          filters: plan.filters,
          portfolios: plan.portfolios,
          wants_votes: plan.wants_votes,
          speakers: plan.speakers,
          interpretation: plan.interpretation,
          group_by: plan.group_by,
          compare: plan.compare,
        }
      : undefined,
    history,
    follows: follows || undefined,
  };
}

export interface StreamEvent {
  type: string;
  [k: string]: unknown;
}

/** POST /api/ask and yield server-sent events as they arrive. */
export async function* askStream(body: unknown, signal?: AbortSignal): AsyncGenerator<StreamEvent> {
  const resp = await post("/api/ask", body, signal);
  if (!resp.ok || !resp.body) {
    await json(resp);
    return;
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx: number;
    while ((idx = buffer.indexOf("\n\n")) >= 0) {
      const chunk = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      yield* parseChunk(chunk);
    }
  }
  if (buffer.trim()) yield* parseChunk(buffer);
}

export function* parseChunk(chunk: string): Generator<StreamEvent> {
  const data = chunk
    .split("\n")
    .filter((l) => l.startsWith("data: "))
    .map((l) => l.slice(6))
    .join("\n");
  if (data) yield JSON.parse(data) as StreamEvent;
}
