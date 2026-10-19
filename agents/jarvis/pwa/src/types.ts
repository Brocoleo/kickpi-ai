export type AuthMe = {
  authenticated: boolean;
  method: "token" | "session" | null;
  password_configured: boolean;
  session_ttl_days: number;
  version: string;
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
};

export type StoredMessage = {
  id: number;
  role: string;
  content: string | null;
  tool_calls: string | null;
  created_at: string;
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
};
