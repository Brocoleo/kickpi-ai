import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Chat } from "./Chat";
import { ApiError, type ChatStreamHandlers } from "../api";

/**
 * Adjuntos del chat: el archivo se sube a Entrada/ del NAS y la ruta viaja en
 * el mensaje para que el modelo pueda encontrarlo con `nas_search`.
 */

const apiMock = vi.hoisted(() => ({
  conversations: vi.fn(),
  conversation: vi.fn(),
  chatStream: vi.fn(),
  stopChat: vi.fn(),
  deleteConversation: vi.fn(),
  uploadFile: vi.fn(),
}));

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return {
    ...actual,
    api: apiMock,
    ApiError: actual.ApiError,
  };
});

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  Object.values(apiMock).forEach((fn) => fn.mockReset());
  apiMock.conversations.mockResolvedValue({ conversations: [], count: 0 });
  apiMock.conversation.mockResolvedValue({
    conversation_id: "c1",
    messages: [],
  });
});

describe("Chat · adjuntos", () => {
  it("sube el archivo y menciona la ruta en el mensaje", async () => {
    const subido = {
      ok: true,
      path: "Entrada/20261022-1200-abcd-factura.pdf",
      name: "factura.pdf",
      size: 4,
    };
    apiMock.uploadFile.mockResolvedValue(subido);
    let enviado = "";
    apiMock.chatStream.mockImplementation(
      (mensaje: string, _conv: string | null, h: ChatStreamHandlers) => {
        enviado = mensaje;
        h.onMeta("c1");
        return new Promise<void>(() => {
          /* queda abierto durante la prueba */
        });
      },
    );

    const user = userEvent.setup();
    const { container } = render(<Chat onUnauthorized={() => {}} csrfToken="csrf-1" />);
    await screen.findByText("Hola.");

    const input = screen.getByLabelText("Archivo para adjuntar");
    const archivo = new File(["%PDF"], "factura.pdf", {
      type: "application/pdf",
    });
    fireEvent.change(input, { target: { files: [archivo] } });

    await screen.findByText("factura.pdf");
    expect(apiMock.uploadFile).toHaveBeenCalledWith(archivo, "csrf-1");

    // Con adjunto se puede enviar aunque no haya texto.
    const enviar = screen.getByLabelText("Enviar mensaje") as HTMLButtonElement;
    expect(enviar.disabled).toBe(false);

    await user.type(screen.getByPlaceholderText("Escribe a Jarvis…"), "mira esto");
    await user.click(enviar);

    await waitFor(() =>
      expect(enviado).toContain("Adjunto en la NAS: Entrada/20261022-1200-abcd-factura.pdf"),
    );
    expect(enviado).toContain("mira esto");
    // El chip se limpia al enviar.
    await waitFor(() => expect(container.querySelector(".attach-chip")).toBeNull());
  });

  it("muestra el error del servidor y no deja el chip", async () => {
    apiMock.uploadFile.mockRejectedValue(
      new ApiError(413, "El archivo supera el límite de 50 MB"),
    );

    render(<Chat onUnauthorized={() => {}} csrfToken="csrf-1" />);
    await screen.findByText("Hola.");

    const input = screen.getByLabelText("Archivo para adjuntar");
    fireEvent.change(input, {
      target: { files: [new File(["x"], "grande.bin")] },
    });

    expect(
      await screen.findByText("El archivo supera el límite de 50 MB"),
    ).toBeTruthy();
    expect(screen.queryByText("grande.bin")).toBeNull();
  });

  it("rechaza adjuntos de más de 50 MB sin llamar al servidor", async () => {
    render(<Chat onUnauthorized={() => {}} csrfToken="csrf-1" />);
    await screen.findByText("Hola.");

    const grande = new File(["x"], "enorme.bin");
    Object.defineProperty(grande, "size", { value: 51 * 1024 * 1024 });
    fireEvent.change(screen.getByLabelText("Archivo para adjuntar"), {
      target: { files: [grande] },
    });

    expect(await screen.findByText("Máximo 50 MB por archivo")).toBeTruthy();
    expect(apiMock.uploadFile).not.toHaveBeenCalled();
  });
});
