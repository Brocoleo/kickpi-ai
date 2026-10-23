import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Chat } from "./Chat";
import type { ChatStreamHandlers } from "../api";
import type { Conversation, StoredMessage } from "../types";

/**
 * Iteración «chat mejor» (0.11.0):
 * - panel de conversaciones (abrir otra, borrar),
 * - detener un turno pidiéndolo al servidor (no solo cortando el fetch),
 * - marca de «interrumpido» en el texto parcial,
 * - actividad de herramienta EN VIVO mientras se ejecuta.
 */

const apiMock = vi.hoisted(() => ({
  nasConfig: vi.fn(),
  conversations: vi.fn(),
  conversation: vi.fn(),
  chatStream: vi.fn(),
  stopChat: vi.fn(),
  deleteConversation: vi.fn(),
}));

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return { ...actual, api: apiMock };
});

const AHORA = new Date().toISOString();

function conv(overrides: Partial<Conversation>): Conversation {
  return {
    id: "c1",
    title: "Primera",
    created_at: AHORA,
    updated_at: AHORA,
    message_count: 2,
    preview: "hola",
    ...overrides,
  };
}

function fila(overrides: Partial<StoredMessage>): StoredMessage {
  return {
    id: 1,
    role: "user",
    content: "",
    tool_calls: null,
    tool_call_id: null,
    name: null,
    created_at: AHORA,
    ...overrides,
  };
}

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  Object.values(apiMock).forEach((fn) => fn.mockReset());
  apiMock.nasConfig.mockResolvedValue({ upload_max_mb: 50 });
  apiMock.stopChat.mockResolvedValue({ ok: true, stopped: true });
  apiMock.deleteConversation.mockResolvedValue({ ok: true, conversation_id: "c2" });
});

describe("Chat · conversaciones", () => {
  it("abre otra conversación desde el historial", async () => {
    apiMock.conversations.mockResolvedValue({
      conversations: [conv({ id: "c1" }), conv({ id: "c2", title: "Segunda" })],
      count: 2,
    });
    apiMock.conversation.mockImplementation(async (id: string) => ({
      conversation_id: id,
      messages: [fila({ id: 1, role: "user", content: `mensaje de ${id}` })],
    }));

    const user = userEvent.setup();
    render(<Chat onUnauthorized={() => {}} />);
    await screen.findByText("mensaje de c1");

    await user.click(screen.getByLabelText("Conversaciones"));
    expect(screen.getByText("Segunda")).toBeTruthy();

    await user.click(screen.getByText("Segunda"));
    await screen.findByText("mensaje de c2");
    expect(apiMock.conversation).toHaveBeenCalledWith("c2");
  });

  it("borra una conversación en dos pasos, con CSRF", async () => {
    apiMock.conversations.mockResolvedValue({
      conversations: [conv({ id: "c1" }), conv({ id: "c2", title: "Segunda" })],
      count: 2,
    });
    apiMock.conversation.mockResolvedValue({
      conversation_id: "c1",
      messages: [fila({ role: "assistant", content: "hola" })],
    });

    const user = userEvent.setup();
    render(<Chat onUnauthorized={() => {}} csrfToken="csrf-123" />);
    await screen.findByText("hola");

    await user.click(screen.getByLabelText("Conversaciones"));
    await user.click(screen.getAllByLabelText("Borrar conversación")[1]);
    await user.click(screen.getByText("Borrar"));

    await waitFor(() =>
      expect(apiMock.deleteConversation).toHaveBeenCalledWith("c2", "csrf-123"),
    );
  });
});

describe("Chat · detener y actividad en vivo", () => {
  it("detener pide al servidor y marca el texto como interrumpido", async () => {
    apiMock.conversations.mockResolvedValue({ conversations: [], count: 0 });
    apiMock.conversation.mockResolvedValue({
      conversation_id: "c1",
      messages: [],
    });

    let handlers: ChatStreamHandlers | null = null;
    let terminar: (() => void) | null = null;
    apiMock.chatStream.mockImplementation(
      (
        _mensaje: string,
        _conv: string | null,
        h: ChatStreamHandlers,
      ): Promise<void> => {
        handlers = h;
        h.onMeta("c1");
        return new Promise<void>((resolve) => {
          terminar = resolve;
        });
      },
    );

    const user = userEvent.setup();
    render(<Chat onUnauthorized={() => {}} />);
    await screen.findByText("Hola.");

    await user.type(screen.getByPlaceholderText("Escribe a Jarvis…"), "hola{enter}");
    await waitFor(() => expect(handlers).not.toBeNull());

    act(() => {
      handlers!.onSegment();
      handlers!.onToken("Estoy mirando");
    });
    await screen.findByText("Estoy mirando");

    await user.click(screen.getByLabelText("Detener la respuesta"));
    expect(apiMock.stopChat).toHaveBeenCalledWith("c1");
    // Enviar el botón NO corta la conexión: el servidor cierra con `done`.
    expect(terminar).not.toBeNull();

    act(() => {
      handlers!.onDone({
        conversation_id: "c1",
        reply: "Estoy mirando",
        tool_trace: [],
        model: null,
        interrupted: true,
      });
      terminar!();
    });

    expect(await screen.findByText("interrumpido")).toBeTruthy();
  });

  it("muestra la herramienta en curso antes de su resultado", async () => {
    apiMock.conversations.mockResolvedValue({ conversations: [], count: 0 });
    apiMock.conversation.mockResolvedValue({
      conversation_id: "c1",
      messages: [],
    });

    let handlers: ChatStreamHandlers | null = null;
    apiMock.chatStream.mockImplementation(
      (
        _mensaje: string,
        _conv: string | null,
        h: ChatStreamHandlers,
      ): Promise<void> => {
        handlers = h;
        h.onMeta("c1");
        return new Promise<void>(() => {
          /* queda abierto mientras dure la prueba */
        });
      },
    );

    const user = userEvent.setup();
    render(<Chat onUnauthorized={() => {}} />);
    await screen.findByText("Hola.");

    await user.type(screen.getByPlaceholderText("Escribe a Jarvis…"), "cpu{enter}");
    await waitFor(() => expect(handlers).not.toBeNull());

    act(() => {
      handlers!.onToolStart("disk_usage");
    });
    expect(await screen.findByText("disk_usage")).toBeTruthy();
    expect(screen.getByText(/Usando/)).toBeTruthy();

    act(() => {
      handlers!.onTool({
        tool: "disk_usage",
        ok: true,
        duration_ms: 20,
        summary: "/ al 40%",
      });
    });
    await waitFor(() =>
      expect(screen.queryByText(/Usando/)).toBeNull(),
    );
    expect(await screen.findByText("disk_usage")).toBeTruthy();
  });
});
