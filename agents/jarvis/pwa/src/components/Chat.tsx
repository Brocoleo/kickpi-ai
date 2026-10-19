import { useEffect, useRef, useState } from "react";
import { ApiError, api } from "../api";
import { shortModel } from "../lib/format";
import { RichText } from "../lib/markdown";
import type { ToolTrace } from "../types";
import { PlusIcon, SendIcon } from "./icons";

const STORAGE_KEY = "jarvis_conversation_id";

type Message = {
  id: string;
  role: "user" | "assistant" | "error";
  content: string;
  tools?: ToolTrace[];
  model?: string | null;
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

export function Chat({ onUnauthorized }: { onUnauthorized: () => void }) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(
    () => sessionStorage.getItem(STORAGE_KEY),
  );
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);

  const scrollRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Recupera el historial de la conversación de esta pestaña.
  useEffect(() => {
    const stored = sessionStorage.getItem(STORAGE_KEY);
    if (!stored) return;
    let cancelled = false;

    api
      .conversation(stored)
      .then(({ messages: storedMessages }) => {
        if (cancelled) return;
        const mapped: Message[] = storedMessages
          .filter(
            (m) =>
              (m.role === "user" || m.role === "assistant") &&
              (m.content ?? "").trim().length > 0,
          )
          .map((m) => ({
            id: `h-${m.id}`,
            role: m.role as "user" | "assistant",
            content: m.content ?? "",
          }));
        if (mapped.length > 0) setMessages(mapped);
        else sessionStorage.removeItem(STORAGE_KEY);
      })
      .catch((err) => {
        if (err instanceof ApiError && err.status === 401) onUnauthorized();
      });

    return () => {
      cancelled = true;
    };
  }, [onUnauthorized]);

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
    setMessages((prev) => [
      ...prev,
      { id: uid(), role: "user", content },
    ]);
    setSending(true);

    try {
      const response = await api.chat(content, conversationId);
      setConversationId(response.conversation_id);
      sessionStorage.setItem(STORAGE_KEY, response.conversation_id);
      setMessages((prev) => [
        ...prev,
        {
          id: uid(),
          role: "assistant",
          content: response.reply,
          tools: response.tool_trace,
          model: response.model,
        },
      ]);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        onUnauthorized();
        return;
      }
      const detail = err instanceof Error ? err.message : "Error desconocido";
      setMessages((prev) => [
        ...prev,
        {
          id: uid(),
          role: "error",
          content: `No pude completar la consulta: ${detail}`,
        },
      ]);
    } finally {
      setSending(false);
    }
  }

  function newConversation() {
    setMessages([]);
    setConversationId(null);
    sessionStorage.removeItem(STORAGE_KEY);
    setInput("");
    textareaRef.current?.focus();
  }

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

          {messages.map((message) => (
            <div key={message.id} className={`msg ${message.role}`}>
              {message.role !== "error" && (
                <span className="msg-role">
                  {message.role === "user" ? "Tú" : "Jarvis"}
                </span>
              )}
              <div className="bubble">
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
          ))}

          {sending && (
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
