const BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

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
    list: () => request<unknown[]>("/api/research/"),
    get: (id: string) => request<unknown>(`/api/research/${id}`),
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
      request<unknown[]>(`/api/documents/${status ? `?status=${status}` : ""}`),
    get: (id: string) => request<unknown>(`/api/documents/${id}`),
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
    list: () => request<unknown[]>("/api/reports/"),
    get: (id: string) => request<unknown>(`/api/reports/${id}`),
    update: (id: string, patch: { title?: string; content?: string; status?: string }) =>
      request<unknown>(`/api/reports/${id}`, {
        method: "PATCH",
        body: JSON.stringify(patch),
      }),
    delete: (id: string) =>
      request<{ deleted: string }>(`/api/reports/${id}`, { method: "DELETE" }),
    exportUrl: (id: string) => `${BASE_URL}/api/reports/${id}/export`,
  },

  analysis: {
    start: () => `${BASE_URL}/api/analysis/start`, // SSE URL
    list: () => request<unknown[]>("/api/analysis/"),
    get: (id: string) => request<unknown>(`/api/analysis/${id}`),
    exportUrl: (id: string) => `${BASE_URL}/api/analysis/${id}/export`,
    delete: (id: string) => request<{ deleted: string }>(`/api/analysis/${id}`, { method: "DELETE" }),
  },

  settings: {
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

  auth: {
    status: () => request<{ auth_required: boolean }>("/api/auth/status"),
    login: (password: string) =>
      request<{ token: string; auth_required: boolean; expires_in?: number }>(
        "/api/auth/login",
        { method: "POST", body: JSON.stringify({ password }) }
      ),
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

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // Parse SSE lines
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";

    let currentEvent = "message";
    let currentData = "";

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
