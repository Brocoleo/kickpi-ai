import type {
  AuthMe,
  ChatResponse,
  Containers,
  Disks,
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

export const api = {
  me: () => request<AuthMe>("/auth/me"),

  login: (password: string) =>
    request<{ ok: boolean; expires_days: number }>("/auth/login", {
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
