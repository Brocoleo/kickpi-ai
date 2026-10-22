export type AuthMe = {
  authenticated: boolean;
  method: "token" | "session" | null;
  password_configured: boolean;
  session_ttl_days: number;
  version: string;
  /** Token CSRF ligado a esta sesion. Vive SOLO en memoria: nunca en
   *  localStorage ni en una cookie legible por JavaScript. */
  csrf_token?: string | null;
  /** Identidad humana de la sesion (quien aprueba). */
  user?: string | null;
};

export type ToolTrace = {
  tool: string;
  arguments?: Record<string, unknown>;
  ok: boolean;
  duration_ms: number;
  summary?: string;
};

export type ChatResponse = {
  conversation_id: string;
  reply: string;
  tool_trace: ToolTrace[];
  model: string | null;
  /** Diagnóstico del turno (solo en el cierre del stream). */
  elapsed_ms?: number;
  tools_count?: number;
};

export type ToolCall = {
  id?: string;
  type?: string;
  function: { name: string; arguments: string };
};

export type StoredMessage = {
  id: number;
  role: string;
  content: string | null;
  tool_calls: ToolCall[] | null;
  tool_call_id: string | null;
  name: string | null;
  created_at: string;
  /** Metadatos de la sonda, solo en mensajes de rol `tool`. */
  tool_ok?: boolean;
  tool_duration_ms?: number;
  tool_summary?: string;
  /** Turno cortado por el usuario (o por una desconexión) a medias. */
  interrupted?: boolean;
};

/** Fila de la lista de conversaciones (sin abrir el historial completo). */
export type Conversation = {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  message_count: number;
  /** Último mensaje del usuario, en una línea. */
  preview: string;
};

export type SystemStats = {
  cpu: {
    model: string | null;
    cores: number;
    physical_cores: number;
    percent_total: number;
    percent_per_core: number[];
    load_avg: [number, number, number];
    temperature_c: number | null;
  };
  memory: {
    total_bytes: number;
    used_bytes: number;
    available_bytes: number;
    percent: number;
  };
  swap: { total_bytes: number; used_bytes: number; percent: number };
  uptime_seconds: number;
  boot_time: number;
};

export type DiskInfo = {
  mount: string;
  total_bytes: number;
  used_bytes: number;
  free_bytes: number;
  percent: number;
};

export type Disks = { disks: DiskInfo[] };

export type ContainerInfo = {
  name: string;
  image: string;
  state: string;
  health: string | null;
  exit_code: number | null;
  ports: string[];
  restart_count: number;
};

export type Containers = { containers: ContainerInfo[]; count: number };

export type LlmStatus = {
  configured: boolean;
  base_url: string;
  models: string[];
  circuit_breaker: Record<
    string,
    { open: boolean; reopens_in_s: number; failures: number; last_error: string | null }
  >;
  policy: Record<string, string | number>;
};

export type Health = {
  status: string;
  version: string;
  llm_configured: boolean;
  llm_model: string;
  tools_reachable: boolean;
  pwa: boolean;
  static_asset?: string | null;
};

/* --- Equipos (ejecutor multi-host) ------------------------------------- */

export type Equipo = {
  id: string;
  label: string;
  transport: "local" | "ssh";
  enabled: boolean;
  address: string | null;
  os: string;
  roots: string[];
  online?: boolean;
  reason?: string;
  kernel?: string;
  latency_ms?: number;
};

export type Ejecucion = {
  id: number;
  created_at: string;
  host_id: string;
  operation: string;
  params_json: string | null;
  allowed: number;
  ok: number;
  exit_code: number | null;
  duration_ms: number | null;
  output_excerpt: string | null;
  error: string | null;
};

export type Equipos = {
  hosts: Equipo[];
  executions: Ejecucion[];
  count: number;
};

/* --- Aprobaciones humanas (Fase C) -------------------------------------- */

export type ApprovalStatus =
  | "pending"
  | "approved"
  | "rejected"
  | "expired"
  | "executing"
  | "consumed"
  | "failed";

export type Approval = {
  id: string;
  created_at: string;
  /** Quien la solicita: siempre el agente. */
  requested_by: string;
  /** Quien la aprueba: una persona autenticada. */
  approved_by: string | null;
  host: string;
  project_id: string | null;
  operation: string;
  params: Record<string, unknown>;
  /** argv NORMALIZADO: lo que se ejecutara si se aprueba. */
  argv: string[];
  resolved_path: string | null;
  timeout_s: number | null;
  request_hash: string;
  decision_hash: string;
  risk: string | null;
  risk_description: string | null;
  expires_at: string;
  status: ApprovalStatus;
  decided_at: string | null;
  executed_at: string | null;
  exit_code: number | null;
  duration_ms: number | null;
  result_error: string | null;
};

export type Approvals = {
  approvals: Approval[];
  count: number;
};
