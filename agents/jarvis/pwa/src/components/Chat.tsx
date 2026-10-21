import { useEffect, useMemo, useRef, useState } from "react";
import { ApiError, api } from "../api";
import { shortModel } from "../lib/format";
import { RichText } from "../lib/markdown";
import type { StoredMessage, ToolCall, ToolTrace } from "../types";
import { PlusIcon, SendIcon } from "./icons";
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
  /** Fecha ISO del mensaje (para separar por dia y mostrar la hora). */
  at?: string;
  /** Burbuja que sigue recibiendo tokens por SSE. */
  streaming?: boolean;
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

function hora(iso?: string): string | null {
  if (!iso) return null;
  const fecha = new Date(iso);
  if (Number.isNaN(fecha.getTime())) return null;
  return fecha.toLocaleTimeString("es-ES", { hour: "2-digit", minute: "2-digit" });
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

// `scrollTo` no existe en todos los entornos (jsdom, navegadores viejos):
// sin la alternativa, el efecto rompería al pintar el historial.
function alFinal(el: HTMLElement, suave = true): void {
  if (typeof el.scrollTo === "function") {
    el.scrollTo({ top: el.scrollHeight, behavior: suave ? "smooth" : "auto" });
  } else {
    el.scrollTop = el.scrollHeight;
  }
}

export function Chat({ onUnauthorized }: { onUnauthorized: () => void }) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  // Turno pendiente de respuesta (p.ej. aprobacion recien ejecutada).
  const [awaitingReply, setAwaitingReply] = useState(false);
  const [atBottom, setAtBottom] = useState(true);
  const [menuAbierto, setMenuAbierto] = useState(false);
  const [avisoCopia, setAvisoCopia] = useState<string | null>(null);

  const scrollRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  // Si el usuario está leyendo arriba, no se le arrastra al final.
  const pegadoAbajo = useRef(true);
  // Historial de lo escrito en esta sesión, para recordarlo con ↑.
  const historial = useRef<string[]>([]);
  const [indiceHistorial, setIndiceHistorial] = useState(-1);

  // Restaura la conversacion: primero la ultima usada; si no hay id (iOS
  // reinicio la PWA), la mas reciente del servidor.
  useEffect(() => {
    let cancelled = false;

    const load = async (id: string): Promise<boolean> => {
      try {
        const { messages: stored } = await api.conversation(id);
        if (cancelled) return true;
        const mapped = mapStored(stored);
        if (mapped.length > 0) setMessages(mapped);
        const last = stored[stored.length - 1];
        setAwaitingReply(last?.role === "tool");
        return true;
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) onUnauthorized();
        return false;
      }
    };

    const restoreLatest = async () => {
      try {
        const { conversations } = await api.conversations();
        if (cancelled || conversations.length === 0) return;
        const latest = conversations[0];
        rememberConversation(latest.id);
        setConversationId(latest.id);
        await load(latest.id);
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) onUnauthorized();
      }
    };

    const stored = readConversationId();
    if (stored) {
      setConversationId(stored);
      void load(stored).then((ok) => {
        if (!ok) void restoreLatest();
      });
    } else {
      void restoreLatest();
    }

    return () => {
      cancelled = true;
    };
  }, [onUnauthorized]);

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
    const content = (text ?? input).trim();
    if (!content || sending) return;

    historial.current = [...historial.current, content].slice(-50);
    setIndiceHistorial(-1);
    setInput("");
    setAwaitingReply(false);
    pegadoAbajo.current = true;
    setMessages((prev) => [
      ...prev,
      { id: uid(), role: "user", content, at: new Date().toISOString() },
    ]);
    setSending(true);
    const controller = new AbortController();
    abortRef.current = controller;

    try {
      await api.chatStream(
        content,
        conversationId,
        {
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
          onTool: (tool: ToolTrace) => {
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
            rememberConversation(data.conversation_id);
            setConversationId(data.conversation_id);
            setMessages((prev) => {
              const next = prev.map((message) =>
                message.streaming ? { ...message, streaming: false } : message,
              );
              const streamed = next
                .filter((message) => message.role === "assistant")
                .map((message) => message.content)
                .join("\n");
              // Respuestas que no pasan por el stream (nodo "limit" o
              // fallback del proveedor): se muestran igualmente.
              if (data.reply && !streamed.includes(data.reply)) {
                next.push({
                  id: uid(),
                  role: "assistant",
                  content: data.reply,
                  model: data.model,
                  at: new Date().toISOString(),
                });
              }
              return next;
            });
            setAwaitingReply(false);
            // Reconciliacion con el historial persistido (fuente de verdad).
            void api
              .conversation(data.conversation_id)
              .then(({ messages: stored }) => {
                const mapped = mapStored(stored);
                if (mapped.length > 0) setMessages(mapped);
                const last = stored[stored.length - 1];
                setAwaitingReply(last?.role === "tool");
              })
              .catch(() => {
                /* el stream ya mostro la respuesta */
              });
          },
          onError: (detail) => {
            setMessages((prev) => [
              ...prev.map((message) =>
                message.streaming ? { ...message, streaming: false } : message,
              ),
              {
                id: uid(),
                role: "error",
                content: `No pude completar la consulta: ${detail}`,
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
          at: new Date().toISOString(),
        },
      ]);
    } finally {
      abortRef.current = null;
      setSending(false);
      setMessages((prev) =>
        prev.map((message) =>
          message.streaming ? { ...message, streaming: false } : message,
        ),
      );
    }
  }

  function newConversation() {
    abortRef.current?.abort();
    setMessages([]);
    setConversationId(null);
    setAwaitingReply(false);
    localStorage.removeItem(STORAGE_KEY);
    sessionStorage.removeItem(STORAGE_KEY);
    setInput("");
    setIndiceHistorial(-1);
    textareaRef.current?.focus();
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

  const showThinking =
    sending &&
    !messages.some(
      (message) => message.streaming && message.content.trim().length > 0,
    );

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
            if (message.streaming && !message.content) return null;
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
                {message.role === "assistant" && message.model && (
                  <span className="msg-model">{shortModel(message.model)}</span>
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
        <button
          className="send-btn"
          type="submit"
          disabled={!input.trim() || sending}
          aria-label="Enviar mensaje"
        >
          <SendIcon />
        </button>
      </form>
    </div>
  );
}
