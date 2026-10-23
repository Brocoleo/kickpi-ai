import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
} from "react";
import { ApiError, api } from "../api";
import { shortModel } from "../lib/format";
import { RichText } from "../lib/markdown";
import type {
  Conversation,
  StoredMessage,
  ToolCall,
  ToolTrace,
} from "../types";
import type { UploadBatchResult, UploadResult } from "../api";
import {
  HistoryIcon,
  PaperclipIcon,
  PlusIcon,
  RefreshIcon,
  SendIcon,
  TrashIcon,
  XIcon,
} from "./icons";
import { ToolActivity, type ToolStep } from "./ToolActivity";

/** El id de conversacion NO es un secreto: localStorage evita perderlo si
 *  iOS suspende la PWA. sessionStorage se conserva solo como migracion. */
const STORAGE_KEY = "jarvis_conversation_id";

type Message = {
  id: string;
  role: "user" | "assistant" | "error";
  content: string;
  tools?: ToolStep[];
  model?: string | null;
  /** Diagnóstico del turno, enviado por el agente al cerrar. */
  elapsedMs?: number;
  toolsCount?: number;
  /** Texto que originó un error, para poder reintentarlo. */
  retry?: string;
  /** Fecha ISO del mensaje (para separar por dia y mostrar la hora). */
  at?: string;
  /** Burbuja que sigue recibiendo tokens por SSE. */
  streaming?: boolean;
  /** El turno se cortó a medias: el texto no es una respuesta completa. */
  interrupted?: boolean;
};

const SUGGESTIONS = [
  "¿Cómo está el sistema?",
  "¿Qué contenedores están corriendo?",
  "¿Cuánto espacio queda en disco?",
];

function uid(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

/** Argumentos de una tool_call a texto legible (nunca una línea kilométrica). */
function prettyArgs(raw: string | undefined | Record<string, unknown>): string | undefined {
  if (raw === undefined) return undefined;
  try {
    const parsed = typeof raw === "string" ? JSON.parse(raw) : raw;
    const texto = JSON.stringify(parsed, null, 2);
    return texto === "{}" ? undefined : texto;
  } catch {
    return typeof raw === "string" ? raw : undefined;
  }
}

/** Resumen de un resultado crudo cuando no hay `tool_summary` guardado. */
function resumenDeResultado(content: string | null): string | undefined {
  if (!content) return undefined;
  try {
    const data = JSON.parse(content);
    if (typeof data !== "object" || data === null) return undefined;
    if (data.error) return String(data.error);
    if (data.state === "pending_approval") return "Pendiente de aprobación";
    const claves = Object.keys(data).slice(0, 4);
    return claves.length > 0 ? claves.join(", ") : undefined;
  } catch {
    return undefined;
  }
}

/**
 * Historial persistido → burbujas.
 *
 * El backend devuelve TODAS las filas, incluidas las de rol `tool`. Aquí se
 * reagrupan: cada llamada se crea con los datos del `assistant` que la pidió
 * y se completa en cuanto llega su resultado (mismo objeto, mutado in situ).
 */
export function mapStored(stored: StoredMessage[]): Message[] {
  const salida: Message[] = [];
  const porLlamada = new Map<string, ToolStep>();

  for (const fila of stored) {
    if (fila.role === "tool") {
      const paso = porLlamada.get(fila.tool_call_id ?? "");
      if (!paso) continue;
      paso.ok = fila.tool_ok ?? !/"(error|denied)"\s*:/.test(fila.content ?? "");
      if (fila.tool_duration_ms !== undefined) {
        paso.duration_ms = fila.tool_duration_ms;
      }
      paso.summary = fila.tool_summary || resumenDeResultado(fila.content);
      continue;
    }

    if (fila.role !== "user" && fila.role !== "assistant") continue;

    const content = (fila.content ?? "").trim();
    const calls: ToolCall[] = fila.tool_calls ?? [];
    // Un assistant con tool_calls pero sin texto sigue siendo parte del
    // turno: lleva la actividad de herramientas. Sin ambas cosas, se omite.
    if (!content && calls.length === 0) continue;

    const pasos: ToolStep[] = calls.map((call, indice) => {
      const paso: ToolStep = {
        id: call.id ?? `h-${fila.id}-${indice}`,
        tool: call.function?.name ?? "herramienta",
        ok: true,
        args: prettyArgs(call.function?.arguments),
      };
      if (call.id) porLlamada.set(call.id, paso);
      return paso;
    });

    salida.push({
      id: `h-${fila.id}`,
      role: fila.role,
      content,
      at: fila.created_at,
      tools: pasos.length > 0 ? pasos : undefined,
      interrupted: fila.interrupted || undefined,
    });
  }

  return salida;
}

function readConversationId(): string | null {
  const stored =
    localStorage.getItem(STORAGE_KEY) ?? sessionStorage.getItem(STORAGE_KEY);
  sessionStorage.removeItem(STORAGE_KEY);
  if (stored) localStorage.setItem(STORAGE_KEY, stored);
  return stored;
}

function rememberConversation(id: string): void {
  localStorage.setItem(STORAGE_KEY, id);
  sessionStorage.removeItem(STORAGE_KEY);
}

// --------------------------------------------------------------------------- //
// Fechas
// --------------------------------------------------------------------------- //
function mismoDia(a: Date, b: Date): boolean {
  return (
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
  );
}

function etiquetaDia(iso: string): string {
  const fecha = new Date(iso);
  if (Number.isNaN(fecha.getTime())) return "";
  const hoy = new Date();
  if (mismoDia(fecha, hoy)) return "Hoy";
  const ayer = new Date(hoy);
  ayer.setDate(hoy.getDate() - 1);
  if (mismoDia(fecha, ayer)) return "Ayer";
  return fecha.toLocaleDateString("es-ES", { day: "numeric", month: "long" });
}

function duracion(ms?: number): string | null {
  if (ms == null || !Number.isFinite(ms)) return null;
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(1)} s`;
}

function hora(iso?: string): string | null {
  if (!iso) return null;
  const fecha = new Date(iso);
  if (Number.isNaN(fecha.getTime())) return null;
  return fecha.toLocaleTimeString("es-ES", { hour: "2-digit", minute: "2-digit" });
}

/** Tamaño legible para el chip del adjunto. */
function peso(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
/** Texto derecho del chip: estado, o carpeta y peso cuando ya está listo. */
function detalleAdjunto(adjunto: AdjuntoPendiente): string {
  if (adjunto.estado === "error") return "Error";
  if (adjunto.estado === "cola") return "En cola";
  if (adjunto.estado === "subiendo") return "Subiendo…";
  const carpeta = adjunto.resultado?.folder
    ? `${adjunto.resultado.folder} · `
    : "";
  return `${carpeta}${peso(adjunto.resultado?.size ?? adjunto.file.size)}`;
}

/**
 * Reparte la selección en lotes por cantidad **y por peso**.
 *
 * El endpoint corta en 500 MB por petición y deshace el lote entero si se
 * pasa: mejor mandar 2 lotes de 300 MB que uno de 600 que se rechaza.
 */
function repartirLotes(
  adjuntos: AdjuntoPendiente[],
  tamanoMaximo: number,
): AdjuntoPendiente[][] {
  const lotes: AdjuntoPendiente[][] = [];
  let actual: AdjuntoPendiente[] = [];
  let bytes = 0;
  for (const adjunto of adjuntos) {
    const lleno =
      actual.length >= tamanoMaximo ||
      (actual.length > 0 && bytes + adjunto.file.size > LOTE_MAXIMO_BYTES);
    if (lleno) {
      lotes.push(actual);
      actual = [];
      bytes = 0;
    }
    actual.push(adjunto);
    bytes += adjunto.file.size;
  }
  if (actual.length > 0) lotes.push(actual);
  return lotes;
}/** Fecha compacta para la lista de conversaciones. */
function fechaLista(iso: string): string {
  const fecha = new Date(iso);
  if (Number.isNaN(fecha.getTime())) return "";
  const ahora = new Date();
  if (mismoDia(fecha, ahora)) return "hoy";
  const ayer = new Date(ahora);
  ayer.setDate(ahora.getDate() - 1);
  if (mismoDia(fecha, ayer)) return "ayer";
  return fecha.toLocaleDateString("es-ES", { day: "numeric", month: "short" });
}

// --------------------------------------------------------------------------- //
// Exportación
// --------------------------------------------------------------------------- //
function aMarkdown(mensajes: Message[], conversationId: string | null): string {
  const lineas: string[] = ["# Conversación con Jarvis", ""];
  if (conversationId) lineas.push(`Conversación: \`${conversationId}\``, "");

  for (const mensaje of mensajes) {
    const quien =
      mensaje.role === "user" ? "Tú" : mensaje.role === "error" ? "Error" : "Jarvis";
    const fecha = hora(mensaje.at);
    lineas.push(`## ${quien}${fecha ? ` · ${fecha}` : ""}`, "");

    if (mensaje.tools?.length) {
      for (const paso of mensaje.tools) {
        const ms = paso.duration_ms !== undefined ? ` (${paso.duration_ms} ms)` : "";
        const detalle = paso.summary ? ` — ${paso.summary}` : "";
        lineas.push(`- \`${paso.tool}\` ${paso.ok ? "ok" : "falló"}${ms}${detalle}`);
      }
      lineas.push("");
    }
    if (mensaje.content) lineas.push(mensaje.content, "");
  }
  return lineas.join("\n");
}

function aJson(mensajes: Message[], conversationId: string | null): string {
  return JSON.stringify(
    {
      exported_at: new Date().toISOString(),
      conversation_id: conversationId,
      messages: mensajes.map((mensaje) => ({
        role: mensaje.role,
        at: mensaje.at ?? null,
        model: mensaje.model ?? null,
        content: mensaje.content,
        tools: mensaje.tools ?? [],
      })),
    },
    null,
    2,
  );
}

function descargar(nombre: string, tipo: string, contenido: string): void {
  const blob = new Blob([contenido], { type: tipo });
  const url = URL.createObjectURL(blob);
  const enlace = document.createElement("a");
  enlace.href = url;
  enlace.download = nombre;
  document.body.appendChild(enlace);
  enlace.click();
  enlace.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function nombreArchivo(extension: string): string {
  const ahora = new Date();
  const dos = (n: number) => String(n).padStart(2, "0");
  const marca = `${ahora.getFullYear()}${dos(ahora.getMonth() + 1)}${dos(
    ahora.getDate(),
  )}-${dos(ahora.getHours())}${dos(ahora.getMinutes())}`;
  return `jarvis-${marca}.${extension}`;
}

/** Archivo elegido por el usuario y su estado de subida (chips del compositor). */
type AdjuntoPendiente = {
  id: string;
  file: File;
  estado: "cola" | "subiendo" | "listo" | "error";
  resultado?: UploadResult;
  error?: string;
};

/** Avance de una tanda de subida (barra sobre los chips). */
type ProgresoSubida = {
  /** Archivos ya resueltos (listos o con error). */
  hechos: number;
  total: number;
  /** Lote en curso (1-based) y cuántos lotes tiene la tanda. */
  lote: number;
  lotes: number;
  /** Archivos que van en el lote en curso (para la fracción de bytes). */
  enLote: number;
};

/** Tope de archivos por mensaje: 100 fotos de una vez es el caso real. */
const MAX_ADJUNTOS = 100;
/** Archivos por petición: 10 como máximo, para no armar cuerpos enormes. */
const LOTE_MAXIMO = 10;
/**
 * Peso por petición. Más bajo que el corte del servidor (500 MB) a propósito:
 * con lotes de ~20 MB la barra avanza cada pocos segundos y una subida lenta
 * por Tailscale no parece colgada.
 */
const LOTE_MAXIMO_BYTES = 20 * 1024 * 1024;

/** API de wake lock (no está en todos los lib.dom ni en todos los navegadores). */
type WakeLockSimple = { release: () => Promise<void> };

async function mantenerPantallaActiva(): Promise<WakeLockSimple | null> {
  try {
    const nav = navigator as Navigator & {
      wakeLock?: { request: (tipo: "screen") => Promise<WakeLockSimple> };
    };
    if (!nav.wakeLock) return null;
    return await nav.wakeLock.request("screen");
  } catch {
    // Sin permiso, pestaña oculta o iOS viejo: se sube igual, sin wake lock.
    return null;
  }
}

function soltarPantalla(wake: WakeLockSimple | null): void {
  if (!wake) return;
  void wake.release().catch(() => {
    /* ya liberado o pestaña cerrada */
  });
}

/** Reintentos ante 429 (límite por IP) antes de marcar el lote en error. */
const REINTENTOS_429 = 3;
/** Tope de espera que se acepta del `Retry-After` (no dormir 10 minutos). */
const ESPERA_MAXIMA_S = 120;
/** Respaldo si la config del NAS aún no llegó. */
const MAX_ADJUNTOS_MB = 50;

// `scrollTo` no existe en todos los entornos (jsdom, navegadores viejos):
// sin la alternativa, el efecto rompería al pintar el historial.
function alFinal(el: HTMLElement, suave = true): void {
  if (typeof el.scrollTo === "function") {
    el.scrollTo({ top: el.scrollHeight, behavior: suave ? "smooth" : "auto" });
  } else {
    el.scrollTop = el.scrollHeight;
  }
}

export function Chat({
  onUnauthorized,
  csrfToken = null,
}: {
  onUnauthorized: () => void;
  csrfToken?: string | null;
}) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  // Turno pendiente de respuesta (p.ej. aprobacion recien ejecutada).
  const [awaitingReply, setAwaitingReply] = useState(false);
  const [atBottom, setAtBottom] = useState(true);
  const [menuAbierto, setMenuAbierto] = useState(false);
  const [avisoCopia, setAvisoCopia] = useState<string | null>(null);
  // Lista de conversaciones (panel de historial) y borrado en dos pasos.
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  // Paginación del historial: 50 por página, sin traer todo de golpe.
  const [historyHasMore, setHistoryHasMore] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  // Herramienta ejecutándose AHORA (evento tool_start): se muestra en vivo
  // dentro de la burbuja, no como un paso ya terminado.
  const [liveTool, setLiveTool] = useState<string | null>(null);
  // Adjuntos del chat: ya subidos a Entrada/ del NAS. Se mencionan sus rutas
  // en el mensaje para que el modelo no tenga que buscarlos a ciegas.
  const [adjuntos, setAdjuntos] = useState<AdjuntoPendiente[]>([]);
  const [uploadError, setUploadError] = useState<string | null>(null);
  // Avance del lote en curso (null cuando no se está subiendo nada).
  const [progreso, setProgreso] = useState<ProgresoSubida | null>(null);
  // Segundos que se está esperando por el límite de subidas (429).
  const [espera, setEspera] = useState<number | null>(null);
  // Se está preparando la selección (iOS materializa 100 File y bloquea).
  const [procesandoSeleccion, setProcesandoSeleccion] = useState(false);
  // Bytes del lote en vuelo, informados por el XHR (mueve la barra dentro
  // del lote; sin esto la subida de 20 MB parecía congelada).
  const [avanceLote, setAvanceLote] = useState<{
    cargado: number;
    total: number;
  } | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  // Límite real por archivo: lo fija Ajustes → NAS (la subida lo revalida).
  const [limiteMb, setLimiteMb] = useState(MAX_ADJUNTOS_MB);
  // Archivos por petición: nunca más que el freno por minuto, o el lote
  // entero se rechazaría (413 «no cabe»). Si el ajuste cambia, se relee al
  // recargar la PWA.
  const [tamanoLote, setTamanoLote] = useState(LOTE_MAXIMO);
  const subiendo = adjuntos.some(
    (a) => a.estado === "subiendo" || a.estado === "cola",
  );
  const adjuntosListos = adjuntos.filter(
    (a) => a.estado === "listo" && a.resultado,
  );

  const scrollRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  // Si el usuario está leyendo arriba, no se le arrastra al final.
  const pegadoAbajo = useRef(true);
  // Historial de lo escrito en esta sesión, para recordarlo con ↑.
  const historial = useRef<string[]>([]);
  const [indiceHistorial, setIndiceHistorial] = useState(-1);

  const refreshConversations = useCallback(async (): Promise<Conversation[]> => {
    try {
      const data = await api.conversations(0, 50);
      // Defensivo: un backend viejo (o un mock) puede no traer la lista.
      const items = Array.isArray(data?.conversations) ? data.conversations : [];
      setConversations(items);
      setHistoryHasMore(Boolean(data?.has_more));
      return items;
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) onUnauthorized();
      return [];
    }
  }, [onUnauthorized]);

  const loadMoreConversations = useCallback(async () => {
    if (historyLoading) return;
    setHistoryLoading(true);
    try {
      const data = await api.conversations(conversations.length, 50);
      const items = Array.isArray(data?.conversations) ? data.conversations : [];
      setConversations((prev) => {
        const vistos = new Set(prev.map((item) => item.id));
        return [...prev, ...items.filter((item) => !vistos.has(item.id))];
      });
      setHistoryHasMore(Boolean(data?.has_more));
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) onUnauthorized();
    } finally {
      setHistoryLoading(false);
    }
  }, [conversations.length, historyLoading, onUnauthorized]);

  const loadConversation = useCallback(
    async (id: string): Promise<boolean> => {
      try {
        const data = await api.conversation(id);
        const stored = Array.isArray(data?.messages) ? data.messages : [];
        setMessages(mapStored(stored));
        const last = stored[stored.length - 1];
        setAwaitingReply(last?.role === "tool");
        setConversationId(id);
        rememberConversation(id);
        return true;
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) onUnauthorized();
        return false;
      }
    },
    [onUnauthorized],
  );

  // El límite por archivo puede haberse subido desde Ajustes → NAS. Si la
  // consulta falla, se conserva el respaldo local (50 MB).
  useEffect(() => {
    api
      .nasConfig()
      .then((config) => {
        setLimiteMb(config.upload_max_mb);
        const porMinuto = config.upload_rate_per_min;
        setTamanoLote(
          porMinuto > 0 ? Math.min(LOTE_MAXIMO, porMinuto) : LOTE_MAXIMO,
        );
      })
      .catch(() => {
        /* sin drama: la subida revalida en el servidor */
      });
  }, []);

  // Restaura la conversacion: primero la ultima usada; si no hay id (iOS
  // reinicio la PWA), la mas reciente del servidor.
  useEffect(() => {
    let cancelled = false;

    const restoreLatest = async () => {
      const items = await refreshConversations();
      if (cancelled || items.length === 0) return;
      await loadConversation(items[0].id);
    };

    const stored = readConversationId();
    if (stored) {
      // La lista se necesita para el panel de historial, aunque el hilo se
      // restaure desde el id guardado.
      void refreshConversations();
      void loadConversation(stored).then((ok) => {
        if (!ok) void restoreLatest();
      });
    } else {
      void restoreLatest();
    }

    return () => {
      cancelled = true;
    };
  }, [refreshConversations, loadConversation]);

  // Si el ultimo mensaje persistido es una herramienta, el turno aun se esta
  // cerrando (p.ej. el agente reanuda tras aprobar). Se refresca solo.
  useEffect(() => {
    if (!awaitingReply || !conversationId || sending) return;
    let cancelled = false;
    let tries = 0;

    const tick = async () => {
      tries += 1;
      try {
        const { messages: stored } = await api.conversation(conversationId);
        if (cancelled) return;
        const mapped = mapStored(stored);
        if (mapped.length > 0) setMessages(mapped);
        const last = stored[stored.length - 1];
        if (last && last.role === "assistant" && (last.content ?? "").trim()) {
          setAwaitingReply(false);
        }
      } catch {
        /* se reintenta en el siguiente ciclo */
      }
      if (tries >= 30) setAwaitingReply(false);
    };

    void tick();
    const timer = window.setInterval(() => {
      if (tries >= 30) {
        window.clearInterval(timer);
        return;
      }
      void tick();
    }, 2_500);

    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [awaitingReply, conversationId, sending]);

  useEffect(() => {
    if (!pegadoAbajo.current) return;
    const el = scrollRef.current;
    if (!el) return;
    alFinal(el);
  }, [messages, sending]);

  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 120)}px`;
  }, [input]);

  // Cierra el menú de exportar al tocar fuera.
  useEffect(() => {
    if (!menuAbierto) return;
    const cerrar = () => setMenuAbierto(false);
    window.addEventListener("pointerdown", cerrar);
    return () => window.removeEventListener("pointerdown", cerrar);
  }, [menuAbierto]);

  function alDesplazar(): void {
    const el = scrollRef.current;
    if (!el) return;
    const cerca = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    pegadoAbajo.current = cerca;
    setAtBottom(cerca);
  }

  function irAlFinal(): void {
    const el = scrollRef.current;
    if (!el) return;
    pegadoAbajo.current = true;
    setAtBottom(true);
    alFinal(el);
  }

  async function send(text?: string) {
    const notaAdjunto = adjuntosListos.length
      ? "\n\n" +
        adjuntosListos
          .map((a) => `📎 Adjunto en la NAS: ${a.resultado!.path}`)
          .join("\n")
      : "";
    const content = (text ?? `${input}${notaAdjunto}`).trim();
    if (!content || sending || subiendo) return;

    historial.current = [...historial.current, content].slice(-50);
    setIndiceHistorial(-1);
    setInput("");
    // Los chips fallidos se quedan para reintentarlos; los listos ya viajaron.
    setAdjuntos((prev) => prev.filter((a) => a.estado === "error"));
    setUploadError(null);
    setAwaitingReply(false);
    pegadoAbajo.current = true;
    setMessages((prev) => [
      ...prev,
      { id: uid(), role: "user", content, at: new Date().toISOString() },
    ]);
    setSending(true);
    setLiveTool(null);
    const controller = new AbortController();
    abortRef.current = controller;

    try {
      await api.chatStream(
        content,
        conversationId,
        {
          onMeta: (id) => {
            // El id llega antes del primer token: desde ese momento se puede
            // detener el turno aunque la conversación sea nueva.
            rememberConversation(id);
            setConversationId(id);
          },
          onSegment: () => {
            setMessages((prev) => [
              ...prev,
              {
                id: uid(),
                role: "assistant",
                content: "",
                at: new Date().toISOString(),
                streaming: true,
              },
            ]);
          },
          onToken: (chunk) => {
            setMessages((prev) => {
              const next = [...prev];
              for (let index = next.length - 1; index >= 0; index -= 1) {
                if (next[index].streaming) {
                  next[index] = {
                    ...next[index],
                    content: next[index].content + chunk,
                  };
                  return next;
                }
              }
              return [
                ...next,
                {
                  id: uid(),
                  role: "assistant",
                  content: chunk,
                  at: new Date().toISOString(),
                  streaming: true,
                },
              ];
            });
          },
          onToolStart: (tool: string) => {
            setLiveTool(tool);
            // Si el modelo llamó a una herramienta sin narrar nada, no hay
            // burbuja donde colgar la actividad: se crea vacía ahora para
            // que el resultado aparezca en su sitio al terminar.
            setMessages((prev) => {
              if (prev.some((message) => message.streaming)) return prev;
              return [
                ...prev,
                {
                  id: uid(),
                  role: "assistant",
                  content: "",
                  at: new Date().toISOString(),
                  streaming: true,
                },
              ];
            });
          },
          onTool: (tool: ToolTrace) => {
            setLiveTool(null);
            const paso: ToolStep = {
              id: uid(),
              tool: tool.tool,
              ok: tool.ok,
              duration_ms: tool.duration_ms,
              summary: tool.summary,
              args: prettyArgs(tool.arguments),
            };
            setMessages((prev) => {
              const next = [...prev];
              for (let index = next.length - 1; index >= 0; index -= 1) {
                if (next[index].role === "assistant") {
                  next[index] = {
                    ...next[index],
                    tools: [...(next[index].tools ?? []), paso],
                  };
                  return next;
                }
              }
              return next;
            });
          },
          onDone: (data) => {
            setLiveTool(null);
            rememberConversation(data.conversation_id);
            setConversationId(data.conversation_id);
            setMessages((prev) => {
              const next = prev.map((message) =>
                message.streaming
                  ? {
                      ...message,
                      streaming: false,
                      interrupted: data.interrupted || undefined,
                    }
                  : message,
              );
              const streamed = next
                .filter((message) => message.role === "assistant")
                .map((message) => message.content)
                .join("\n");
              // Respuestas que no pasan por el stream (nodo "limit",
              // fallback del proveedor o turno detenido antes del primer
              // token): se muestran igualmente.
              if (data.reply && !streamed.includes(data.reply)) {
                next.push({
                  id: uid(),
                  role: "assistant",
                  content: data.reply,
                  at: new Date().toISOString(),
                  interrupted: data.interrupted || undefined,
                });
              }
              // El diagnóstico va en la última burbuja de Jarvis.
              for (let index = next.length - 1; index >= 0; index -= 1) {
                if (next[index].role === "assistant") {
                  next[index] = {
                    ...next[index],
                    model: data.model ?? next[index].model,
                    elapsedMs: data.elapsed_ms,
                    toolsCount: data.tools_count ?? next[index].tools?.length ?? 0,
                  };
                  break;
                }
              }
              return next;
            });
            setAwaitingReply(false);
            // La lista sí se refresca: cambia el título/vista previa de la
            // conversación y, en una nueva, aparece por primera vez.
            void refreshConversations();
            // No se vuelve a pedir el historial al cerrar: el stream ya es
            // la fuente de verdad de este turno y el GET solo provocaba un
            // parpadeo (reemplazaba burbujas idénticas por otras nuevas) y
            // perdía el diagnóstico recién recibido.
          },
          onError: (detail) => {
            setLiveTool(null);
            setMessages((prev) => [
              ...prev.map((message) =>
                message.streaming ? { ...message, streaming: false } : message,
              ),
              {
                id: uid(),
                role: "error",
                content: `No pude completar la consulta: ${detail}`,
                retry: content,
                at: new Date().toISOString(),
              },
            ]);
          },
        },
        controller.signal,
      );
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        onUnauthorized();
        return;
      }
      if (err instanceof DOMException && err.name === "AbortError") return;
      const detail = err instanceof Error ? err.message : "Error desconocido";
      setMessages((prev) => [
        ...prev.map((message) =>
          message.streaming ? { ...message, streaming: false } : message,
        ),
        {
          id: uid(),
          role: "error",
          content: `No pude completar la consulta: ${detail}`,
          retry: content,
          at: new Date().toISOString(),
        },
      ]);
    } finally {
      abortRef.current = null;
      setSending(false);
      setLiveTool(null);
      setMessages((prev) =>
        prev.map((message) =>
          message.streaming ? { ...message, streaming: false } : message,
        ),
      );
    }
  }

  /**
   * Detiene el turno en curso.
   *
   * Se pide al servidor (no basta con cortar el fetch: así el agente guarda
   * el texto parcial y la conversación queda coherente). Si aún no hay id de
   * conversación —no debería, llega en `meta`— se corta la conexión.
   */
  function quitarAdjunto(id: string): void {
    setAdjuntos((prev) => prev.filter((a) => a.id !== id));
  }

  /**
   * Sube los archivos elegidos en lotes (`tamanoLote`), uno detrás de otro.
   *
   * Cada lote es UNA petición al endpoint batch. El servidor procesa sus
   * archivos en serie y devuelve el resultado de cada uno: si uno falla, los
   * demás siguen. La barra muestra «Lote X de Y · hechos/total».
   *
   * Si el lote no cabe en el límite por IP (429), se espera lo que pida
   * `Retry-After` y se reintenta ese mismo lote; el resto espera su turno.
   * Nada de esto se guarda en disco hasta que el servidor lo confirma.
   */
  async function attachFile(event: ChangeEvent<HTMLInputElement>): Promise<void> {
    const elegidos = Array.from(event.target.files ?? []);
    event.target.value = "";
    if (!elegidos.length) return;

    setUploadError(null);
    // Con 100 fotos, iOS tarda en materializar los `File`. Se avisa antes de
    // recorrerlos y se cede un frame (rAF + timeout) para que el aviso llegue
    // a pintarse: sin esto la pantalla se quedaba congelada sin señal de vida.
    setProcesandoSeleccion(true);
    await new Promise<void>((listo) => {
      if (typeof requestAnimationFrame === "function") {
        requestAnimationFrame(() => window.setTimeout(listo, 0));
      } else {
        window.setTimeout(listo, 0);
      }
    });

    const nuevos: AdjuntoPendiente[] = [];
    let grandes = 0;
    let sobrantes = 0;
    try {
      for (const file of elegidos) {
        if (file.size > limiteMb * 1024 * 1024) {
          grandes += 1;
          continue;
        }
        if (adjuntos.length + nuevos.length >= MAX_ADJUNTOS) {
          sobrantes += 1;
          continue;
        }
        nuevos.push({ id: uid(), file, estado: "cola" });
      }
    } finally {
      setProcesandoSeleccion(false);
    }
    const avisos: string[] = [];
    if (grandes) avisos.push(`Máximo ${limiteMb} MB por archivo`);
    if (sobrantes) avisos.push(`Máximo ${MAX_ADJUNTOS} archivos por mensaje`);
    if (avisos.length) setUploadError(avisos.join(" · "));
    if (!nuevos.length) return;
    setAdjuntos((prev) => [...prev, ...nuevos]);

    const lotes = repartirLotes(nuevos, tamanoLote);
    let hechos = 0;
    setProgreso({
      hechos: 0,
      total: nuevos.length,
      lote: 1,
      lotes: lotes.length,
      enLote: lotes[0]?.length ?? 0,
    });

    /** Vuelca el resultado del lote en sus chips (por id, no por posición). */
    const marcarLote = (
      lote: AdjuntoPendiente[],
      respuesta: UploadBatchResult,
    ): void => {
      setAdjuntos((prev) =>
        prev.map((chip) => {
          const indice = lote.findIndex((item) => item.id === chip.id);
          if (indice < 0) return chip;
          const resultado = respuesta.results[indice];
          if (!resultado) {
            return {
              ...chip,
              estado: "error",
              error: "El servidor no devolvió resultado para este archivo",
            };
          }
          return resultado.ok
            ? { ...chip, estado: "listo", resultado }
            : { ...chip, estado: "error", error: resultado.error };
        }),
      );
    };

    try {
      const wake = await mantenerPantallaActiva();
      try {
        for (let indice = 0; indice < lotes.length; indice += 1) {
          const lote = lotes[indice];
          const numero = indice + 1;
          // Los chips del lote pasan de «en cola» a «subiendo» al arrancar.
          const ids = new Set(lote.map((item) => item.id));
          setAdjuntos((prev) =>
            prev.map((chip) =>
              ids.has(chip.id) && chip.estado === "cola"
                ? { ...chip, estado: "subiendo" }
                : chip,
            ),
          );
          setProgreso({
            hechos,
            total: nuevos.length,
            lote: numero,
            lotes: lotes.length,
            enLote: lote.length,
          });

          // El XHR dispara onprogress muchas veces por segundo: se limita a
          // ~8 avisos/s para no provocar un render por cada paquete.
          let ultimoAviso = 0;
          const alAvanzar = (cargado: number, total: number): void => {
            const ahora = Date.now();
            if (ahora - ultimoAviso < 120 && cargado < total) return;
            ultimoAviso = ahora;
            setAvanceLote({ cargado, total });
          };

          let respuesta: UploadBatchResult | null = null;
          let ultimoError: unknown = null;
          for (let intento = 1; intento <= REINTENTOS_429; intento += 1) {
            try {
              respuesta = await api.uploadBatch(
                lote.map((item) => item.file),
                csrfToken,
                alAvanzar,
              );
              break;
            } catch (err) {
              ultimoError = err;
              if (err instanceof ApiError && err.status === 401) {
                onUnauthorized();
                throw err;
              }
              const segundos =
                err instanceof ApiError && err.status === 429
                  ? Math.min(
                      ESPERA_MAXIMA_S,
                      Math.max(0, err.retryAfter ?? 30),
                    )
                  : 0;
              if (segundos <= 0 || intento === REINTENTOS_429) throw err;
              setEspera(segundos);
              await new Promise<void>((listo) =>
                window.setTimeout(listo, segundos * 1000),
              );
              setEspera(null);
            }
          }
          if (!respuesta) throw ultimoError ?? new Error("No se pudo subir el lote");

          setAvanceLote(null);
          marcarLote(lote, respuesta);
          hechos += lote.length;
          setProgreso({
            hechos,
            total: nuevos.length,
            lote: numero,
            lotes: lotes.length,
            enLote: lote.length,
          });
        }
      } finally {
        soltarPantalla(wake);
      }
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) onUnauthorized();
      const mensaje =
        err instanceof Error ? err.message : "No se pudo subir el archivo";
      // Un fallo del lote deja en error lo que siga pendiente: el usuario ve
      // exactamente qué no llegó y puede quitarlo o reintentarlo.
      setAdjuntos((prev) =>
        prev.map((chip) =>
          chip.estado === "subiendo" || chip.estado === "cola"
            ? { ...chip, estado: "error", error: mensaje }
            : chip,
        ),
      );
    } finally {
      setEspera(null);
      setProgreso(null);
      setAvanceLote(null);
    }
  }

  async function stopTurn(): Promise<void> {
    const id = conversationId;
    if (!id) {
      abortRef.current?.abort();
      return;
    }
    try {
      // Si el servidor no contesta en un par de segundos, no se deja al
      // usuario esperando: se corta la conexión y el agente guardará lo que
      // ya había enviado.
      await Promise.race([
        api.stopChat(id),
        new Promise((_, reject) =>
          window.setTimeout(() => reject(new Error("timeout")), 2500),
        ),
      ]);
    } catch {
      abortRef.current?.abort();
    }
  }

  function newConversation() {
    abortRef.current?.abort();
    setMessages([]);
    setConversationId(null);
    setAwaitingReply(false);
    setLiveTool(null);
    setAdjuntos([]);
    setUploadError(null);
    setProgreso(null);
    setEspera(null);
    setAvanceLote(null);
    setProcesandoSeleccion(false);
    localStorage.removeItem(STORAGE_KEY);
    sessionStorage.removeItem(STORAGE_KEY);
    setInput("");
    setIndiceHistorial(-1);
    setHistoryOpen(false);
    textareaRef.current?.focus();
  }

  async function openConversation(id: string): Promise<void> {
    if (id === conversationId) {
      setHistoryOpen(false);
      return;
    }
    // Cambiar de conversación con un turno en marcha lo corta en local (para
    // que no lleguen eventos a la vista nueva) y avisa al servidor para que
    // guarde el texto parcial en la conversación de origen.
    if (sending) {
      abortRef.current?.abort();
      if (conversationId) {
        try {
          await api.stopChat(conversationId);
        } catch {
          /* la desconexión ya dejó el turno en pausa */
        }
      }
    }
    setHistoryOpen(false);
    setConfirmDelete(null);
    setAwaitingReply(false);
    await loadConversation(id);
  }

  async function removeConversation(id: string): Promise<void> {
    try {
      await api.deleteConversation(id, csrfToken);
    } catch {
      setAvisoCopia("No se pudo borrar");
      window.setTimeout(() => setAvisoCopia(null), 2000);
      return;
    }
    setConfirmDelete(null);
    if (id === conversationId) {
      newConversation();
    }
    await refreshConversations();
  }

  async function copiarMarkdown(): Promise<void> {
    const texto = aMarkdown(messages, conversationId);
    try {
      await navigator.clipboard.writeText(texto);
      setAvisoCopia("Copiado");
    } catch {
      setAvisoCopia("No se pudo copiar");
    }
    window.setTimeout(() => setAvisoCopia(null), 2000);
  }

  function recordarAnterior(): void {
    const previos = historial.current;
    if (previos.length === 0) return;
    const siguiente =
      indiceHistorial < 0 ? previos.length - 1 : Math.max(0, indiceHistorial - 1);
    setIndiceHistorial(siguiente);
    setInput(previos[siguiente]);
  }

  function recordarSiguiente(): void {
    const previos = historial.current;
    if (indiceHistorial < 0) return;
    const siguiente = indiceHistorial + 1;
    if (siguiente >= previos.length) {
      setIndiceHistorial(-1);
      setInput("");
      return;
    }
    setIndiceHistorial(siguiente);
    setInput(previos[siguiente]);
  }

  // Hay una burbuja escribiendo: el indicador de «pensando» sobra.
  const hayBurbuja = messages.some(
    (message) => message.streaming && message.content.trim().length > 0,
  );
  // Herramienta en curso sin burbuja de texto: se muestra sola, con nombre.
  const showLive = sending && Boolean(liveTool) && !hayBurbuja;
  const showThinking = sending && !hayBurbuja && !liveTool;

  // Avance real de la tanda: archivos resueltos + la fracción de bytes que
  // lleva el lote en vuelo (XHR). Sin esto la barra saltaba de golpe por lote.
  const fraccionLote =
    avanceLote && avanceLote.total > 0 && progreso
      ? Math.min(1, avanceLote.cargado / avanceLote.total) * progreso.enLote
      : 0;
  const porcentajeSubida = progreso
    ? Math.min(
        100,
        Math.round(
          ((progreso.hechos + fraccionLote) / Math.max(1, progreso.total)) * 100,
        ),
      )
    : 0;
  const detalleProgreso =
    espera !== null
      ? ` · esperando ${espera} s`
      : avanceLote
        ? ` · ${peso(avanceLote.cargado)}/${peso(avanceLote.total)}`
        : "";

  // Agrupa por día para no perder la noción del tiempo en un hilo largo.
  const conFechas = useMemo(() => {
    const filas: Array<{ tipo: "dia"; clave: string; etiqueta: string } | {
      tipo: "mensaje";
      mensaje: Message;
    }> = [];
    let ultimoDia: string | null = null;
    for (const mensaje of messages) {
      if (mensaje.at) {
        const fecha = new Date(mensaje.at);
        const clave = `${fecha.getFullYear()}-${fecha.getMonth()}-${fecha.getDate()}`;
        if (clave !== ultimoDia) {
          ultimoDia = clave;
          filas.push({ tipo: "dia", clave, etiqueta: etiquetaDia(mensaje.at) });
        }
      }
      filas.push({ tipo: "mensaje", mensaje });
    }
    return filas;
  }, [messages]);

  return (
    <div className="chat">
      <div className="chat-head">
        <h1 className="screen-title">Chat</h1>
        <div className="chat-actions">
          {avisoCopia && <span className="chat-toast">{avisoCopia}</span>}
          <button
            className="ghost-btn history-btn"
            type="button"
            aria-label="Conversaciones"
            aria-expanded={historyOpen}
            onClick={() => {
              setConfirmDelete(null);
              setHistoryOpen((valor) => !valor);
              void refreshConversations();
            }}
          >
            <HistoryIcon />
            <span className="history-btn-label">Historial</span>
          </button>
          {messages.length > 0 && (
            <div className="chat-menu-wrap">
              <button
                className="ghost-btn"
                type="button"
                aria-expanded={menuAbierto}
                aria-haspopup="menu"
                onPointerDown={(event) => event.stopPropagation()}
                onClick={() => setMenuAbierto((valor) => !valor)}
              >
                Exportar
              </button>
              {menuAbierto && (
                <div
                  className="chat-menu"
                  role="menu"
                  onPointerDown={(event) => event.stopPropagation()}
                >
                  <button
                    type="button"
                    role="menuitem"
                    onClick={() => {
                      setMenuAbierto(false);
                      void copiarMarkdown();
                    }}
                  >
                    Copiar como Markdown
                  </button>
                  <button
                    type="button"
                    role="menuitem"
                    onClick={() => {
                      setMenuAbierto(false);
                      descargar(
                        nombreArchivo("md"),
                        "text/markdown;charset=utf-8",
                        aMarkdown(messages, conversationId),
                      );
                    }}
                  >
                    Descargar Markdown
                  </button>
                  <button
                    type="button"
                    role="menuitem"
                    onClick={() => {
                      setMenuAbierto(false);
                      descargar(
                        nombreArchivo("json"),
                        "application/json",
                        aJson(messages, conversationId),
                      );
                    }}
                  >
                    Descargar JSON
                  </button>
                </div>
              )}
            </div>
          )}
          {messages.length > 0 && (
            <button className="ghost-btn" type="button" onClick={newConversation}>
              <PlusIcon />
              Nueva
            </button>
          )}
        </div>
      </div>

      {historyOpen && (
        <div
          className="history-backdrop"
          onPointerDown={() => setHistoryOpen(false)}
        >
          <aside
            className="history-panel"
            role="dialog"
            aria-label="Conversaciones"
            onPointerDown={(event) => event.stopPropagation()}
          >
            <header className="history-head">
              <h2>Conversaciones</h2>
              <button
                className="icon-btn"
                type="button"
                aria-label="Cerrar"
                onClick={() => setHistoryOpen(false)}
              >
                <XIcon />
              </button>
            </header>
            <div className="history-list">
              {conversations.length === 0 && (
                <p className="history-empty">
                  Aún no hay conversaciones guardadas.
                </p>
              )}
              {conversations.map((item) => (
                <div
                  key={item.id}
                  className={`history-item${
                    item.id === conversationId ? " active" : ""
                  }`}
                >
                  <button
                    type="button"
                    className="history-open"
                    onClick={() => void openConversation(item.id)}
                  >
                    <span className="history-title">
                      {item.title || item.preview || "Conversación"}
                    </span>
                    <span className="history-meta">
                      {fechaLista(item.updated_at)} · {item.message_count}{" "}
                      {item.message_count === 1 ? "mensaje" : "mensajes"}
                    </span>
                  </button>
                  {confirmDelete === item.id ? (
                    <div className="history-confirm">
                      <button
                        type="button"
                        className="ghost-btn history-danger"
                        onClick={() => void removeConversation(item.id)}
                      >
                        Borrar
                      </button>
                      <button
                        type="button"
                        className="ghost-btn"
                        onClick={() => setConfirmDelete(null)}
                      >
                        No
                      </button>
                    </div>
                  ) : (
                    <button
                      type="button"
                      className="icon-btn history-del"
                      aria-label="Borrar conversación"
                      // Borrar el hilo que está respondiendo dejaría el
                      // turno escribiendo en una conversación inexistente.
                      disabled={sending && item.id === conversationId}
                      onClick={() => setConfirmDelete(item.id)}
                    >
                      <TrashIcon />
                    </button>
                  )}
                </div>
              ))}
              {historyHasMore && (
                <button
                  type="button"
                  className="ghost-btn history-more"
                  disabled={historyLoading}
                  onClick={() => void loadMoreConversations()}
                >
                  {historyLoading ? "Cargando…" : "Cargar más"}
                </button>
              )}
            </div>
          </aside>
        </div>
      )}

      <div className="chat-scroll" ref={scrollRef} onScroll={alDesplazar}>
        <div className="chat-inner">
          {messages.length === 0 && (
            <div className="chat-empty">
              <div className="orb" aria-hidden="true" />
              <h2>Hola.</h2>
              <p>
                Estoy conectado a tu KICKPI y puedo consultar el estado real del
                sistema. ¿Qué necesitas?
              </p>
              <div className="suggestions">
                {SUGGESTIONS.map((suggestion) => (
                  <button
                    key={suggestion}
                    type="button"
                    className="chip suggestion"
                    onClick={() => send(suggestion)}
                  >
                    {suggestion}
                  </button>
                ))}
              </div>
            </div>
          )}

          {conFechas.map((fila) => {
            if (fila.tipo === "dia") {
              return (
                <div key={`dia-${fila.clave}`} className="day-sep">
                  <span>{fila.etiqueta}</span>
                </div>
              );
            }

            const message = fila.mensaje;
            // Un segmento vacío aún no tiene nada que mostrar… salvo que
            // lleve actividad de herramientas: entonces sí (es el bloque que
            // se ve mientras el agente trabaja, antes de escribir la
            // respuesta).
            if (message.streaming && !message.content && !message.tools?.length) {
              return null;
            }
            const fecha = hora(message.at);
            return (
              <div key={message.id} className={`msg ${message.role}`}>
                {message.role !== "error" && (message.content || !message.tools?.length) && (
                  <span className="msg-role">
                    {message.role === "user" ? "Tú" : "Jarvis"}
                    {fecha && <time dateTime={message.at}>{fecha}</time>}
                  </span>
                )}
                {message.tools && message.tools.length > 0 && (
                  <ToolActivity steps={message.tools} />
                )}
                {message.content && (
                  <div
                    className={`bubble${message.streaming ? " streaming" : ""}`}
                  >
                    <RichText text={message.content} />
                  </div>
                )}
                {message.streaming && liveTool && message.content && (
                  <span className="live-status" role="status">
                    <span className="live-dot" aria-hidden="true" />
                    Usando <code>{liveTool}</code>…
                  </span>
                )}
                {message.interrupted && (
                  <span className="interrupted-tag">interrumpido</span>
                )}
                {message.role === "error" && message.retry && (
                  <button
                    type="button"
                    className="ghost-btn retry-btn"
                    onClick={() => void send(message.retry)}
                  >
                    Reintentar
                  </button>
                )}
                {message.role === "assistant" &&
                  (message.model || message.elapsedMs != null) && (
                    <span className="msg-model">
                      {message.model ? shortModel(message.model) : "—"}
                      {duracion(message.elapsedMs) && (
                        <> · {duracion(message.elapsedMs)}</>
                      )}
                      {message.toolsCount
                        ? ` · ${message.toolsCount} ${
                            message.toolsCount === 1 ? "herramienta" : "herramientas"
                          }`
                        : ""}
                    </span>
                  )}
              </div>
            );
          })}

          {awaitingReply && (
            <div className="msg assistant">
              <span className="msg-role">Jarvis</span>
              <div className="bubble thinking" aria-label="Cerrando el turno">
                <span />
                <span />
                <span />
              </div>
            </div>
          )}

          {showLive && (
            <div className="msg assistant">
              <span className="msg-role">Jarvis</span>
              <span className="live-status" role="status">
                <span className="live-dot" aria-hidden="true" />
                Usando <code>{liveTool}</code>…
              </span>
            </div>
          )}

          {showThinking && (
            <div className="msg assistant">
              <span className="msg-role">Jarvis</span>
              <div className="bubble thinking" aria-label="Pensando">
                <span />
                <span />
                <span />
              </div>
            </div>
          )}
        </div>
      </div>

      {!atBottom && messages.length > 0 && (
        <button
          type="button"
          className="scroll-bottom"
          aria-label="Ir al final"
          onClick={irAlFinal}
        >
          ↓
        </button>
      )}

      <form
        className="composer"
        onSubmit={(event) => {
          event.preventDefault();
          void send();
        }}
      >
        {procesandoSeleccion && (
          <div className="attach-progress" role="status">
            <span>Preparando la selección…</span>
            <div className="attach-bar" aria-hidden="true">
              <span className="indeterminada" />
            </div>
          </div>
        )}
        {progreso && (
          <div className="attach-progress" role="status">
            <span>
              Lote {progreso.lote} de {progreso.lotes} · {progreso.hechos}/
              {progreso.total}
              {detalleProgreso}
            </span>
            <div className="attach-bar" aria-hidden="true">
              <span style={{ width: `${porcentajeSubida}%` }} />
            </div>
          </div>
        )}
        {adjuntos.length > 0 && (
          <div className="attach-list" aria-live="polite">
            {adjuntos.map((a) => (
              <div key={a.id} className={`attach-chip ${a.estado}`}>
                {a.estado === "subiendo" ? (
                  <RefreshIcon className="spin-icon" />
                ) : (
                  <PaperclipIcon />
                )}
                <span
                  className="attach-name"
                  title={a.resultado?.path ?? a.file.name}
                >
                  {a.file.name}
                </span>
                <span className="attach-where">{detalleAdjunto(a)}</span>
                <button
                  type="button"
                  className="attach-remove"
                  aria-label={`Quitar ${a.file.name}`}
                  onClick={() => quitarAdjunto(a.id)}
                  disabled={sending || subiendo}
                >
                  <XIcon />
                </button>
              </div>
            ))}
          </div>
        )}
        {adjuntos
          .filter((a) => a.estado === "error")
          .map((a) => (
            <div key={`error-${a.id}`} className="attach-error">
              {a.file.name}: {a.error}
            </div>
          ))}
        {uploadError && <div className="attach-error">{uploadError}</div>}
        <input
          ref={fileRef}
          className="attach-input"
          type="file"
          multiple
          aria-label="Archivos para adjuntar"
          onChange={(event) => void attachFile(event)}
          disabled={sending || subiendo || procesandoSeleccion}
        />
        <button
          type="button"
          className="icon-btn attach-btn"
          aria-label="Adjuntar archivos"
          onClick={() => fileRef.current?.click()}
          disabled={sending || subiendo || procesandoSeleccion}
        >
          {subiendo ? <RefreshIcon className="spin-icon" /> : <PaperclipIcon />}
        </button>
        <textarea
          ref={textareaRef}
          rows={1}
          value={input}
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              void send();
              return;
            }
            // ↑/↓ recuerdan lo enviado, sin secuestrar la flecha cuando ya
            // hay texto a medio escribir y el cursor no está al principio.
            if (event.key === "ArrowUp" && (input === "" || indiceHistorial >= 0)) {
              event.preventDefault();
              recordarAnterior();
              return;
            }
            if (event.key === "ArrowDown" && indiceHistorial >= 0) {
              event.preventDefault();
              recordarSiguiente();
            }
          }}
          placeholder="Escribe a Jarvis…"
          enterKeyHint="send"
          disabled={sending}
        />
        {sending ? (
          <button
            className="send-btn stop-btn"
            type="button"
            aria-label="Detener la respuesta"
            onClick={() => void stopTurn()}
          >
            <span aria-hidden="true" />
          </button>
        ) : (
          <button
            className="send-btn"
            type="submit"
            disabled={
              (!input.trim() && adjuntosListos.length === 0) || subiendo
            }
            aria-label="Enviar mensaje"
          >
            <SendIcon />
          </button>
        )}
      </form>
    </div>
  );
}
