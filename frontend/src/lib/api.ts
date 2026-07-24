import type { ResearchSession, Document, Report, RiskAnalysis } from "./types";

const BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

// ── Personas (Multi-Persona Debate: built-in + admin-created custom) ───────
// Shape returned by GET /api/personas/ — see backend/routers/personas.py +
// services/persona_service.get_all_personas. Uniform across built-in and
// custom personas.
export interface PersonaApi {
  key: string;
  name: string;
  title: string;
  initials: string;
  system: string;
  bio: string;
  color: string;      // Tailwind bg-* class
  text_color: string; // Tailwind text-* class
  is_custom: boolean;
}

// Shape returned by GET/POST/PUT /api/admin/personas/ — backend/routers/admin_personas.py.
export interface CustomPersonaApi {
  key: string;
  name: string;
  title: string;
  initials: string;
  color: string;
  text_color: string;
  priorities: string;
  style: string;
  created_by: string;
  created_at: string | null;
  updated_at: string | null;
  is_custom: true;
}

// ── Auth token ──────────────────────────────────────────────────────────────
const TOKEN_KEY = "auth_token";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(TOKEN_KEY);
}
export function setToken(token: string): void {
  if (typeof window !== "undefined") localStorage.setItem(TOKEN_KEY, token);
}
export function clearToken(): void {
  if (typeof window !== "undefined") localStorage.removeItem(TOKEN_KEY);
}
export function authHeaders(): Record<string, string> {
  const t = getToken();
  return t ? { Authorization: `Bearer ${t}` } : {};
}

/** Drop the token and bounce to /login when the server rejects our credentials. */
function handleUnauthorized(): void {
  clearToken();
  if (typeof window !== "undefined" && window.location.pathname !== "/login") {
    window.location.href = "/login";
  }
}

/** fetch() with the bearer token attached and 401 handling. */
export async function authFetch(url: string, options?: RequestInit): Promise<Response> {
  const res = await fetch(url, {
    ...options,
    headers: { ...authHeaders(), ...options?.headers },
  });
  if (res.status === 401) {
    handleUnauthorized();
    throw new Error("Unauthorized");
  }
  return res;
}

/** Download a protected file via an authenticated request + object URL. */
export async function downloadFile(url: string, fallbackName: string): Promise<void> {
  const res = await authFetch(url);
  if (!res.ok) throw new Error(`Download failed: ${res.status}`);
  const blob = await res.blob();
  // Prefer the server-provided filename when it is readable (CORS-exposed).
  let filename = fallbackName;
  const cd = res.headers.get("Content-Disposition");
  const match = cd?.match(/filename="?([^"]+)"?/);
  if (match) filename = match[1];
  const objUrl = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = objUrl;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(objUrl);
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json", ...authHeaders(), ...options?.headers },
    ...options,
  });
  if (res.status === 401) {
    handleUnauthorized();
    throw new Error("Unauthorized");
  }
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`API error ${res.status}: ${text}`);
  }
  return res.json();
}

// ── Research ──────────────────────────────────────────────────────────────────

export const api = {
  research: {
    start: (query: string, maxSources = 8) =>
      request<{ session_id: string }>("/api/research/start", {
        method: "POST",
        body: JSON.stringify({ query, max_sources: maxSources }),
      }),
    list: () => request<ResearchSession[]>("/api/research/"),
    get: (id: string) => request<ResearchSession>(`/api/research/${id}`),
    delete: (id: string) =>
      request<{ deleted: string }>(`/api/research/${id}`, { method: "DELETE" }),
    startUrl: () => `${BASE_URL}/api/research/start`,
    streamUrl: (sessionId: string) => `${BASE_URL}/api/research/${sessionId}/stream`,
    saveToLibrary: (sessionId: string) =>
      request<{ saved: number; collection_id: string }>(
        `/api/research/${sessionId}/save-to-library`,
        { method: "POST" }
      ),
  },

  documents: {
    upload: (file: File) => {
      const form = new FormData();
      form.append("file", file);
      return authFetch(`${BASE_URL}/api/documents/upload`, {
        method: "POST",
        body: form,
      }).then((r) => r.json());
    },
    ingestUrl: (url: string) =>
      request<{ document_id: string; status: string; title: string }>(
        "/api/documents/ingest-url",
        { method: "POST", body: JSON.stringify({ url }) }
      ),
    list: (status?: string) =>
      request<Document[]>(`/api/documents/${status ? `?status=${status}` : ""}`),
    get: (id: string) => request<Document>(`/api/documents/${id}`),
    delete: (id: string) =>
      request<{ deleted: string }>(`/api/documents/${id}`, { method: "DELETE" }),
    assignFolder: (doc_ids: string[], folder_id: string, folder_name: string) =>
      request<{ updated: number }>("/api/documents/assign-folder", {
        method: "POST",
        body: JSON.stringify({ doc_ids, folder_id, folder_name }),
      }),
    renameFolder: (folder_id: string, new_name: string) =>
      request<{ updated: number }>("/api/documents/rename-folder", {
        method: "POST",
        body: JSON.stringify({ folder_id, new_name }),
      }),
    askUrl: () => `${BASE_URL}/api/documents/ask`,
    // Single-document Q&A with API-native citations (see backend
    // services/document_qa.py) — distinct from askUrl above, which searches
    // across the whole/selected library via a tool-use loop.
    askCitationsUrl: (id: string) => `${BASE_URL}/api/documents/${id}/ask-citations`,
  },

  reports: {
    generate: () => `${BASE_URL}/api/reports/generate`, // returns SSE URL, call with POST body
    template: (reportType: string) =>
      request<{ report_type: string; sections: { key: string; title: string; instructions: string }[] }>(
        `/api/reports/template/${reportType}`
      ),
    createDraft: (body: { title: string; report_type: string }) =>
      request<{ report_id: string }>("/api/reports/draft", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    list: () => request<Report[]>("/api/reports/"),
    get: (id: string) => request<Report>(`/api/reports/${id}`),
    update: (id: string, patch: { title?: string; content?: string; status?: string }) =>
      request<Report>(`/api/reports/${id}`, {
        method: "PATCH",
        body: JSON.stringify(patch),
      }),
    delete: (id: string) =>
      request<{ deleted: string }>(`/api/reports/${id}`, { method: "DELETE" }),
    exportUrl: (id: string) => `${BASE_URL}/api/reports/${id}/export`,
  },

  analysis: {
    start: () => `${BASE_URL}/api/analysis/start`, // SSE URL
    list: () => request<RiskAnalysis[]>("/api/analysis/"),
    get: (id: string) => request<RiskAnalysis>(`/api/analysis/${id}`),
    exportUrl: (id: string) => `${BASE_URL}/api/analysis/${id}/export`,
    delete: (id: string) => request<{ deleted: string }>(`/api/analysis/${id}`, { method: "DELETE" }),
  },

  settings: {
    getAvailableModels: () =>
      request<{
        models: { group: string; id: string; label: string }[];
        catalog_updated_at: string | null;
      }>("/api/settings/available-models"),
    getModels: () =>
      request<{
        main_model: string;
        fast_model: string;
        anthropic_api_key: string;
        openai_api_key: string;
        updated_at: string | null;
      }>("/api/settings/models"),
    saveModels: (body: {
      main_model?: string;
      fast_model?: string;
      anthropic_api_key?: string;
      openai_api_key?: string;
    }) =>
      request<{
        main_model: string;
        fast_model: string;
        anthropic_api_key: string;
        openai_api_key: string;
        updated_at: string | null;
      }>("/api/settings/models", {
        method: "PUT",
        body: JSON.stringify(body),
      }),
  },

  reminders: {
    list: () =>
      request<{ id: string; content: string; due_at: string; created_at: string }[]>(
        "/api/reminders/"
      ),
    delete: (id: string) =>
      request<{ deleted: string }>(`/api/reminders/${id}`, { method: "DELETE" }),
  },

  workspace: {
    list: () =>
      request<{ name: string; size_bytes: number; modified_at: string }[]>(
        "/api/workspace"
      ),
    getFile: (name: string) =>
      request<{ name: string; content: string }>(
        `/api/workspace/file?name=${encodeURIComponent(name)}`
      ),
  },

  auth: {
    status: () => request<{ setup_required: boolean }>("/api/auth/status"),
    bootstrap: (email: string, password: string) =>
      request<{ token: string; expires_in: number }>("/api/auth/bootstrap", {
        method: "POST",
        body: JSON.stringify({ email, password }),
      }),
    login: (email: string, password: string) =>
      request<{ token: string; expires_in: number }>("/api/auth/login", {
        method: "POST",
        body: JSON.stringify({ email, password }),
      }),
    me: () => request<{ id: string; email: string; role: "admin" | "member" }>("/api/auth/me"),
    changePassword: (currentPassword: string, newPassword: string) =>
      request<{ ok: boolean }>("/api/auth/me/password", {
        method: "POST",
        body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
      }),
  },

  users: {
    list: () =>
      request<
        {
          id: string;
          email: string;
          role: "admin" | "member";
          is_active: boolean;
          created_at: string;
          last_login_at: string | null;
        }[]
      >("/api/users/"),
    create: (body: { email: string; password: string; role: "admin" | "member" }) =>
      request<{ id: string; email: string; role: string; is_active: boolean }>("/api/users/", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    update: (
      id: string,
      body: { role?: "admin" | "member"; is_active?: boolean; new_password?: string }
    ) =>
      request<{ id: string; email: string; role: string; is_active: boolean }>(
        `/api/users/${id}`,
        { method: "PATCH", body: JSON.stringify(body) }
      ),
  },

  personas: {
    // GET /api/personas/ — open to any authenticated user (built-in + custom,
    // uniformly shaped; see backend/routers/personas.py). Response shape
    // documented on PersonaApi below.
    list: () => request<PersonaApi[]>("/api/personas/"),
    // Admin-only custom-persona management — backend/routers/admin_personas.py.
    // adminList (unlike list above) returns full editable fields
    // (priorities/style), so the admin edit form can be pre-filled.
    adminList: () => request<CustomPersonaApi[]>("/api/admin/personas/"),
    create: (body: { name: string; title: string; initials: string; priorities: string; style: string }) =>
      request<CustomPersonaApi>("/api/admin/personas/", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    update: (
      key: string,
      body: { name: string; title: string; initials: string; priorities: string; style: string }
    ) =>
      request<CustomPersonaApi>(`/api/admin/personas/${key}`, {
        method: "PUT",
        body: JSON.stringify(body),
      }),
    delete: (key: string) =>
      request<{ deleted: string }>(`/api/admin/personas/${key}`, { method: "DELETE" }),
  },

  auditLog: {
    list: (limit = 50, before?: string) =>
      request<
        {
          id: string;
          actor_email: string | null;
          action: string;
          resource_type: string | null;
          resource_id: string | null;
          detail: string | null;
          ip_address: string | null;
          created_at: string;
        }[]
      >(`/api/audit-log/?limit=${limit}${before ? `&before=${encodeURIComponent(before)}` : ""}`),
  },

  datalab: {
    analyzeUrl: () => `${BASE_URL}/api/datalab/analyze`, // multipart POST (file + question), returns SSE
  },

  digest: {
    sendNow: () =>
      request<{
        success: boolean;
        sent_at: string;
        article_count: number;
        recipient: string;
      }>("/api/digest/send-now", { method: "POST" }),
    status: () =>
      request<{
        configured: boolean;
        recipient: string;
        sender: string;
        topics: string[];
        schedule: string;
        current_time_local: string;
        last_sent_at: string | null;
        next_run_at: string | null;
      }>("/api/digest/status"),
    getSettings: () =>
      request<{
        email_to: string;
        email_from: string;
        smtp_password: string;
        topics: string;
        timezone: string;
        send_hour: number;
        updated_at: string | null;
      }>("/api/digest/settings"),
    saveSettings: (body: {
      email_to?: string;
      email_from?: string;
      smtp_password?: string;
      topics?: string;
      timezone?: string;
      send_hour?: number;
    }) =>
      request<{
        email_to: string;
        email_from: string;
        smtp_password: string;
        topics: string;
        timezone: string;
        send_hour: number;
        updated_at: string | null;
      }>("/api/digest/settings", {
        method: "PUT",
        body: JSON.stringify(body),
      }),
  },
};

/**
 * Consume a fetch Response body as an SSE stream.
 * onEvent is called for each parsed event; data lines are JSON-parsed when
 * possible, otherwise passed through as the raw string.
 *
 * Shared by postStream and the research/debate GET streams (this parser used
 * to exist in three copies). Parser state lives outside the read loop so an
 * event whose "event:" and "data:" lines arrive in different network chunks
 * is still attributed to the right event name.
 */
export async function consumeSseStream(
  body: ReadableStream<Uint8Array>,
  onEvent: (event: string, data: unknown) => void
): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let currentEvent = "message";
  let currentData = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // Parse SSE lines
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";

    for (const line of lines) {
      if (line.startsWith("event: ")) {
        currentEvent = line.slice(7).trim();
      } else if (line.startsWith("data: ")) {
        currentData = line.slice(6).trim();
      } else if (line === "") {
        if (currentData) {
          try {
            onEvent(currentEvent, JSON.parse(currentData));
          } catch {
            onEvent(currentEvent, currentData);
          }
          currentEvent = "message";
          currentData = "";
        }
      }
    }
  }
}

/**
 * Consume an SSE stream from a POST endpoint.
 * onEvent is called for each parsed event.
 */
export async function postStream(
  url: string,
  body: unknown,
  onEvent: (event: string, data: unknown) => void,
  signal?: AbortSignal
): Promise<void> {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify(body),
    signal,
  });

  if (res.status === 401) {
    handleUnauthorized();
    throw new Error("Unauthorized");
  }
  if (!res.ok || !res.body) {
    throw new Error(`Stream request failed: ${res.status}`);
  }

  await consumeSseStream(res.body, onEvent);
}

/**
 * Consume an SSE stream from a multipart/form-data POST endpoint (e.g. a
 * file upload). Same event parsing as postStream — no "Content-Type" header
 * is set explicitly so the browser attaches the correct multipart boundary.
 */
export async function postStreamForm(
  url: string,
  form: FormData,
  onEvent: (event: string, data: unknown) => void,
  signal?: AbortSignal
): Promise<void> {
  const res = await fetch(url, {
    method: "POST",
    headers: { ...authHeaders() },
    body: form,
    signal,
  });

  if (res.status === 401) {
    handleUnauthorized();
    throw new Error("Unauthorized");
  }
  if (!res.ok || !res.body) {
    throw new Error(`Stream request failed: ${res.status}`);
  }

  await consumeSseStream(res.body, onEvent);
}
