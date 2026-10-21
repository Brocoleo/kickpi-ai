import { useEffect, useRef, useState } from "react";
import { ApiError, api } from "../api";
import { shortModel } from "../lib/format";
import { RichText } from "../lib/markdown";
import type { StoredMessage, ToolTrace } from "../types";
import { PlusIcon, SendIcon } from "./icons";

/** El id de conversacion NO es un secreto: localStorage evita perderlo si
 *  iOS suspende la PWA. sessionStorage se conserva solo como migracion. */
const STORAGE_KEY = "jarvis_conversation_id";

type Message = {
  id: string;
  role: "user" | "assistant" | "error";
  content: string;
  tools?: ToolTrace[];
  model?: string | null;
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

/** Convierte el historial del backend en burbujas visibles. */
function mapStored(stored: StoredMessage[]): Message[] {
  return stored
    .filter(
      (message) =>
        (message.role === "user" || message.role === "assistant") &&
        (message.content ?? "").trim().length > 0,
    )
    .map((message) => ({
      id: `h-${message.id}`,
      role: message.role as "user" | "assistant",
      content: message.content ?? "",
    }));
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

export function Chat({ onUnauthorized }: { onUnauthorized: () => void }) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  // Turno pendiente de respuesta (p.ej. aprobacion recien ejecutada).
  const [awaitingReply, setAwaitingReply] = useState(false);

  const scrollRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const abortRef = useRef<AbortController | null>(null);

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
    scrollRef.current?.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [messages, sending]);

  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 120)}px`;
  }, [input]);

  async function send(text?: string) {
    const content = (text ?? input).trim();
    if (!content || sending) return;

    setInput("");
    setAwaitingReply(false);
    setMessages((prev) => [...prev, { id: uid(), role: "user", content }]);
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
              { id: uid(), role: "assistant", content: "", streaming: true },
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
                { id: uid(), role: "assistant", content: chunk, streaming: true },
              ];
            });
          },
          onTool: (tool) => {
            setMessages((prev) => {
              const next = [...prev];
              for (let index = next.length - 1; index >= 0; index -= 1) {
                if (next[index].role === "assistant") {
                  next[index] = {
                    ...next[index],
                    tools: [...(next[index].tools ?? []), tool],
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
    textareaRef.current?.focus();
  }

  const showThinking =
    sending &&
    !messages.some(
      (message) => message.streaming && message.content.trim().length > 0,
    );

  return (
    <div className="chat">
      <div className="chat-head">
        <h1 className="screen-title">Chat</h1>
        {messages.length > 0 && (
          <button className="ghost-btn" type="button" onClick={newConversation}>
            <PlusIcon />
            Nueva
          </button>
        )}
      </div>

      <div className="chat-scroll" ref={scrollRef}>
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

          {messages.map((message) => {
            if (message.streaming && !message.content) return null;
            return (
              <div key={message.id} className={`msg ${message.role}`}>
                {message.role !== "error" && (
                  <span className="msg-role">
                    {message.role === "user" ? "Tú" : "Jarvis"}
                  </span>
                )}
                <div
                  className={`bubble${message.streaming ? " streaming" : ""}`}
                >
                  <RichText text={message.content} />
                </div>
                {message.tools && message.tools.length > 0 && (
                  <div className="tool-chips">
                    {message.tools.map((tool, index) => (
                      <span
                        key={`${tool.tool}-${index}`}
                        className={`chip tool ${tool.ok ? "ok" : "fail"}`}
                        title={tool.summary}
                      >
                        {tool.tool}
                        <em>{tool.duration_ms} ms</em>
                      </span>
                    ))}
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
