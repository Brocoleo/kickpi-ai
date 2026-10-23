import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { Chat } from "./Chat";
import type { StoredMessage } from "../types";

/**
 * Humo del historial: al reabrir una conversación debe verse
 *
 *  - el separador de día,
 *  - la actividad de herramientas agrupada (no chips sueltos),
 *  - la respuesta final.
 *
 * Es la parte que antes se perdía por completo: `get_messages` (formato LLM)
 * no traía ni `id` ni fechas, y las herramientas se descartaban al pintar.
 */

const apiMock = vi.hoisted(() => ({
  nasConfig: vi.fn(),
  conversations: vi.fn(),
  conversation: vi.fn(),
  chatStream: vi.fn(),
}));

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return { ...actual, api: apiMock };
});

const HOY = new Date();
HOY.setHours(9, 30, 0, 0);

function fila(overrides: Partial<StoredMessage>): StoredMessage {
  return {
    id: 1,
    role: "user",
    content: "",
    tool_calls: null,
    tool_call_id: null,
    name: null,
    created_at: HOY.toISOString(),
    ...overrides,
  };
}

describe("Chat · historial restaurado", () => {
  beforeEach(() => {
    localStorage.clear();
    apiMock.nasConfig.mockReset();
    apiMock.nasConfig.mockResolvedValue({ upload_max_mb: 50 });
    apiMock.conversations.mockReset();
    apiMock.conversation.mockReset();
  });

  it("muestra fecha, actividad agrupada y respuesta", async () => {
    apiMock.conversations.mockResolvedValue({
      conversations: [{ id: "c1", title: "", updated_at: HOY.toISOString() }],
      count: 1,
    });
    apiMock.conversation.mockResolvedValue({
      conversation_id: "c1",
      messages: [
        fila({ id: 1, role: "user", content: "¿cómo está el sistema?" }),
        fila({
          id: 2,
          role: "assistant",
          content: null,
          tool_calls: [
            {
              id: "call_1",
              function: { name: "system_stats", arguments: "{}" },
            },
          ],
        }),
        fila({
          id: 3,
          role: "tool",
          content: "{}",
          tool_call_id: "call_1",
          name: "system_stats",
          tool_ok: true,
          tool_duration_ms: 12,
          tool_summary: "CPU 5%",
        }),
        fila({ id: 4, role: "assistant", content: "Todo tranquilo." }),
      ],
    });

    render(<Chat onUnauthorized={() => {}} />);

    expect(await screen.findByText("Todo tranquilo.")).toBeTruthy();
    expect(screen.getByText("Hoy")).toBeTruthy();
    expect(screen.getByText("system_stats")).toBeTruthy();
    expect(screen.getByText("CPU 5%")).toBeTruthy();
    // Cabecera del bloque plegable, con el recuento y el tiempo total.
    expect(screen.getByText(/1 acción · 12 ms/)).toBeTruthy();
    // La marca de tiempo también aparece junto al autor.
    expect(screen.getAllByText("09:30").length).toBeGreaterThan(0);
  });

  it("no revienta si la conversación está vacía", async () => {
    apiMock.conversations.mockResolvedValue({ conversations: [], count: 0 });

    render(<Chat onUnauthorized={() => {}} />);

    expect(await screen.findByText("Hola.")).toBeTruthy();
  });
});
