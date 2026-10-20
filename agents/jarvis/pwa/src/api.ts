import type {
  Approval,
  Approvals,
  AuthMe,
  ChatResponse,
  Containers,
  Disks,
  Equipos,
  Health,
  LlmStatus,
  StoredMessage,
  SystemStats,
} from "./types";

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`/api${path}`, {
    credentials: "same-origin",
    cache: "no-store",
    ...init,
    headers: {
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      ...(init.headers ?? {}),
    },
  });

  if (!res.ok) {
    let detail = res.statusText || `HTTP ${res.status}`;
    try {
      const data = (await res.json()) as { detail?: unknown };
      if (typeof data.detail === "string") detail = data.detail;
    } catch {
      /* body no JSON */
    }
    throw new ApiError(res.status, detail);
  }

  return (await res.json()) as T;
}

/**
 * Peticion que cambia estado.
 *
 * Exige la sesion humana (cookie HttpOnly) y el token CSRF ligado a esa
 * sesion. El token viaja en una cabecera y vive SOLO en memoria: nunca se
 * guarda en localStorage ni en sessionStorage.
 */
function mutate<T>(path: string, body?: unknown, csrfToken?: string | null): Promise<T> {
  const headers: Record<string, string> = {};
  if (csrfToken) headers["X-CSRF-Token"] = csrfToken;
  return request<T>(path, {
    method: "POST",
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    headers,
  });
}

export const api = {
  me: () => request<AuthMe>("/auth/me"),

  login: (password: string) =>
    request<{ ok: boolean; expires_days: number; csrf_token?: string }>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ password }),
    }),

  logout: () => request<{ ok: boolean }>("/auth/logout", { method: "POST" }),

  chat: (message: string, conversationId: string | null) =>
    request<ChatResponse>("/chat", {
      method: "POST",
      body: JSON.stringify({ message, conversation_id: conversationId }),
    }),

  tool: <T>(name: string, args: Record<string, unknown> = {}) =>
    request<T>(`/tools/${name}`, {
      method: "POST",
      body: JSON.stringify(args),
    }),

  systemStats: () => api.tool<SystemStats>("system_stats"),
  disks: () => api.tool<Disks>("disk_usage"),
  containers: () => api.tool<Containers>("docker_ps"),
  llmStatus: () => request<LlmStatus>("/llm/status"),

  equipos: () => request<Equipos>("/equipos"),

  /* --- Aprobaciones humanas (Fase C) --------------------------------------
   * Superficie separada de las herramientas del LLM: el backend exige
   * sesion humana + Origin + token CSRF, y el navegador nunca ve el token
   * del ejecutor.
   * ---------------------------------------------------------------------- */
  approvals: (status?: string, limit = 50) => {
    const params = new URLSearchParams();
    if (status) params.set("status", status);
    params.set("limit", String(limit));
    return request<Approvals>(`/aprobaciones?${params.toString()}`);
  },

  approval: (id: string) =>
    request<{ approval: Approval }>(`/aprobaciones/${encodeURIComponent(id)}`),

  approve: (id: string, csrfToken: string | null) =>
    mutate<{ approval: Approval }>(`/aprobaciones/${encodeURIComponent(id)}/aprobar`, undefined, csrfToken),

  reject: (id: string, csrfToken: string | null, reason = "") =>
    mutate<{ approval: Approval }>(
      `/aprobaciones/${encodeURIComponent(id)}/rechazar`,
      { reason },
      csrfToken,
    ),

  execute: (id: string, csrfToken: string | null) =>
    mutate<{
      ok: boolean;
      state: string;
      approval_id: string;
      exit_code: number | null;
      stdout: string;
      stderr: string | null;
      duration_ms: number | null;
      error: string | null;
    }>(`/aprobaciones/${encodeURIComponent(id)}/ejecutar`, undefined, csrfToken),

  conversation: (id: string) =>
    request<{ conversation_id: string; messages: StoredMessage[] }>(
      `/conversations/${encodeURIComponent(id)}`,
    ),

  health: async (): Promise<Health> => {
    const res = await fetch("/health", { cache: "no-store" });
    if (!res.ok) throw new ApiError(res.status, "health no disponible");
    return (await res.json()) as Health;
  },
};
