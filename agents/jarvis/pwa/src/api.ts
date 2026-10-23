import type {
  Approval,
  Approvals,
  AuthMe,
  ChatResponse,
  Containers,
  Conversation,
  Disks,
  Ejecucion,
  Equipos,
  Health,
  LlmStatus,
  NasListing,
  NasRecent,
  NasSearchResult,
  StoredMessage,
  SystemStats,
  ToolTrace,
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

export type ChatStreamDone = {
  conversation_id: string;
  reply: string;
  tool_trace: ToolTrace[];
  model: string | null;
  /** Milisegundos que tardó el turno, medidos por el agente. */
  elapsed_ms?: number;
  /** Cuántas herramientas usó (el servidor ya las contó). */
  tools_count?: number;
  /** El turno se cortó a propósito: el texto es parcial, no una respuesta. */
  interrupted?: boolean;
};

export type UploadResult = {
  ok: boolean;
  /** Ruta relativa a la raíz del NAS, p. ej.
   *  "files/Entrada/Fotos/2026...-foto.jpg". */
  path: string;
  name: string;
  size: number;
};

export type ChatStreamHandlers = {
  /** Id de la conversación, antes de cualquier token (permite detener). */
  onMeta: (conversationId: string) => void;
  onSegment: () => void;
  onToken: (text: string) => void;
  /** Una herramienta empieza a ejecutarse (aún no hay resultado). */
  onToolStart: (tool: string) => void;
  onTool: (tool: ToolTrace) => void;
  onDone: (data: ChatStreamDone) => void;
  onError: (detail: string) => void;
};

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

  /**
   * Chat en streaming (SSE sobre fetch, porque EventSource no permite POST).
   * Los eventos llegan en orden: segment → token* → (tool*) → … → done.
   */
  chatStream: async (
    message: string,
    conversationId: string | null,
    handlers: ChatStreamHandlers,
    signal?: AbortSignal,
  ): Promise<void> => {
    const res = await fetch("/api/chat/stream", {
      method: "POST",
      credentials: "same-origin",
      cache: "no-store",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, conversation_id: conversationId }),
      signal,
    });

    if (!res.ok || !res.body) {
      let detail = res.statusText || `HTTP ${res.status}`;
      try {
        const data = (await res.json()) as { detail?: unknown };
        if (typeof data.detail === "string") detail = data.detail;
      } catch {
        /* respuesta sin JSON */
      }
      throw new ApiError(res.status, detail);
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    // Un stream puede cortarse a media respuesta (red, proveedor, contenedor
    // reiniciado). Sin `done` el turno NO terminó: hay que decirlo, o el
    // usuario leerá una respuesta incompleta creyendo que está completa.
    let cerrado = false;
    let fallo = false;

    const dispatch = (raw: string) => {
      let eventName = "message";
      const dataLines: string[] = [];
      for (const line of raw.split("\n")) {
        if (line.startsWith("event:")) eventName = line.slice(6).trim();
        else if (line.startsWith("data:")) {
          dataLines.push(line.slice(5).replace(/^ /, ""));
        }
      }
      if (dataLines.length === 0) return;
      let payload: Record<string, unknown>;
      try {
        payload = JSON.parse(dataLines.join("\n")) as Record<string, unknown>;
      } catch {
        return;
      }
      switch (eventName) {
        case "meta":
          if (typeof payload.conversation_id === "string") {
            handlers.onMeta(payload.conversation_id);
          }
          break;
        case "segment":
          handlers.onSegment();
          break;
        case "token":
          if (typeof payload.text === "string") handlers.onToken(payload.text);
          break;
        case "tool_start":
          if (typeof payload.tool === "string") handlers.onToolStart(payload.tool);
          break;
        case "tool":
          handlers.onTool(payload as unknown as ToolTrace);
          break;
        case "done":
          cerrado = true;
          handlers.onDone(payload as unknown as ChatStreamDone);
          break;
        case "error":
          fallo = true;
          handlers.onError(
            typeof payload.detail === "string" ? payload.detail : "Error en el chat",
          );
          break;
      }
    };

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let boundary = buffer.indexOf("\n\n");
      while (boundary >= 0) {
        const raw = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        if (raw.trim()) dispatch(raw);
        boundary = buffer.indexOf("\n\n");
      }
    }
    if (buffer.trim()) dispatch(buffer);
    if (!cerrado && !fallo) {
      handlers.onError(
        "La respuesta se cortó antes de terminar (red o proveedor). Reintenta.",
      );
    }
  },

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

  /** Historial del ejecutor (sin sondear hosts). */
  executions: (limit = 20) =>
    request<{ executions: Ejecucion[]; count: number }>(`/executions?limit=${limit}`),

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

  /** Sube un adjunto del chat a la carpeta Entrada del NAS (sesión + CSRF). */
  uploadFile: async (file: File, csrfToken: string | null): Promise<UploadResult> => {
    const form = new FormData();
    form.append("file", file);
    // No se usa `request()`: con FormData el navegador debe poner el
    // Content-Type con el boundary; fijarlo a mano rompe la subida.
    const res = await fetch("/api/files/upload", {
      method: "POST",
      credentials: "same-origin",
      cache: "no-store",
      headers: csrfToken ? { "X-CSRF-Token": csrfToken } : {},
      body: form,
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
    return (await res.json()) as UploadResult;
  },

  /** Detiene el turno en curso. Idempotente: `stopped=false` si ya cerró. */
  stopChat: (conversationId: string) =>
    request<{ ok: boolean; stopped: boolean }>("/chat/stop", {
      method: "POST",
      body: JSON.stringify({ conversation_id: conversationId }),
    }),

  /** NAS nativo de la PWA (solo lectura; el agente hace de proxy). */
  nasList: (path = "", limit = 300) =>
    request<NasListing>(
      `/nas/list?path=${encodeURIComponent(path)}&limit=${limit}`,
    ),

  nasRecent: (limit = 30, path = "") =>
    request<NasRecent>(
      `/nas/recent?limit=${limit}&path=${encodeURIComponent(path)}`,
    ),

  nasSearch: (q: string, content = true, limit = 30) =>
    request<NasSearchResult>(
      `/nas/search?q=${encodeURIComponent(q)}&content=${content}&limit=${limit}`,
    ),

  /** URL de una miniatura o descarga (la sesión viaja en la cookie). */
  nasFileUrl: (path: string, download = false) =>
    `/api/nas/file?path=${encodeURIComponent(path)}${
      download ? "&download=true" : ""
    }`,

  /** Borra un adjunto de Entrada/ (sesión humana + CSRF). */
  deleteNasFile: (path: string, csrfToken: string | null) =>
    request<{ ok: boolean; path: string }>(
      `/files?path=${encodeURIComponent(path)}`,
      {
        method: "DELETE",
        headers: csrfToken ? { "X-CSRF-Token": csrfToken } : {},
      },
    ),

  conversations: (offset = 0, limit = 50) =>
    request<{
      conversations: Conversation[];
      count: number;
      has_more?: boolean;
    }>(`/conversations?offset=${offset}&limit=${limit}`),

  conversation: (id: string) =>
    request<{ conversation_id: string; messages: StoredMessage[] }>(
      `/conversations/${encodeURIComponent(id)}`,
    ),

  /** Borra una conversación. Destructivo: exige sesión humana + CSRF. */
  deleteConversation: (id: string, csrfToken: string | null) =>
    request<{ ok: boolean; conversation_id: string }>(
      `/conversations/${encodeURIComponent(id)}`,
      {
        method: "DELETE",
        headers: csrfToken ? { "X-CSRF-Token": csrfToken } : {},
      },
    ),

  health: async (): Promise<Health> => {
    const res = await fetch("/health", { cache: "no-store" });
    if (!res.ok) throw new ApiError(res.status, "health no disponible");
    return (await res.json()) as Health;
  },
};
