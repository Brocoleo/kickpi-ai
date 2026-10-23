import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Chat } from "./Chat";
import { ApiError, type ChatStreamHandlers } from "../api";

/**
 * Adjuntos del chat: se suben a Entrada/ del NAS y las rutas viajan en el
 * mensaje para que el modelo no tenga que buscarlas a ciegas.
 */

const apiMock = vi.hoisted(() => ({
  nasConfig: vi.fn(),
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
  apiMock.nasConfig.mockResolvedValue({
    inbox: "files/Entrada",
    inbox_dir: "/mnt/data/nas/files/Entrada",
    upload_max_mb: 50,
    upload_rate_per_min: 60,
    classifier_rules: null,
  });
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
      path: "files/Entrada/Documentos/20261022-1200-abcd-factura.pdf",
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

    const input = screen.getByLabelText("Archivos para adjuntar");
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
      expect(enviado).toContain(
        "Adjunto en la NAS: files/Entrada/Documentos/20261022-1200-abcd-factura.pdf",
      ),
    );
    expect(enviado).toContain("mira esto");
    // El chip se limpia al enviar.
    await waitFor(() => expect(container.querySelector(".attach-chip")).toBeNull());
  });

  it("sube varios archivos y menciona cada ruta", async () => {
    const subidos = [
      {
        ok: true,
        path: "files/Entrada/Fotos/a.jpg",
        name: "a.jpg",
        size: 10,
      },
      {
        ok: true,
        path: "files/Entrada/Videos/b.mov",
        name: "b.mov",
        size: 20,
      },
      {
        ok: true,
        path: "files/Entrada/Audio/c.mp3",
        name: "c.mp3",
        size: 30,
      },
    ];
    apiMock.uploadFile
      .mockResolvedValueOnce(subidos[0])
      .mockResolvedValueOnce(subidos[1])
      .mockResolvedValueOnce(subidos[2]);
    let enviado = "";
    apiMock.chatStream.mockImplementation(
      (mensaje: string, _conv: string | null, h: ChatStreamHandlers) => {
        enviado = mensaje;
        h.onMeta("c1");
        return new Promise<void>(() => {});
      },
    );

    const user = userEvent.setup();
    render(<Chat onUnauthorized={() => {}} csrfToken="csrf-1" />);
    await screen.findByText("Hola.");

    const archivos = subidos.map(
      (s) => new File([s.name], s.name, { type: "application/octet-stream" }),
    );
    fireEvent.change(screen.getByLabelText("Archivos para adjuntar"), {
      target: { files: archivos },
    });

    await screen.findByText("a.jpg");
    await screen.findByText("b.mov");
    await screen.findByText("c.mp3");
    await waitFor(() =>
      expect(apiMock.uploadFile).toHaveBeenCalledTimes(3),
    );

    const enviar = screen.getByLabelText("Enviar mensaje") as HTMLButtonElement;
    await waitFor(() => expect(enviar.disabled).toBe(false));
    await user.click(enviar);

    await waitFor(() => {
      for (const subido of subidos) {
        expect(enviado).toContain(`Adjunto en la NAS: ${subido.path}`);
      }
    });
  });

  it("si uno falla, los demás se envían y el fallido se queda marcado", async () => {
    apiMock.uploadFile
      .mockResolvedValueOnce({
        ok: true,
        path: "files/Entrada/Documentos/bien.txt",
        name: "bien.txt",
        size: 3,
      })
      .mockRejectedValueOnce(new ApiError(415, "Extensión no permitida"));
    let enviado = "";
    apiMock.chatStream.mockImplementation(
      (mensaje: string, _conv: string | null, h: ChatStreamHandlers) => {
        enviado = mensaje;
        h.onMeta("c1");
        return new Promise<void>(() => {});
      },
    );

    const user = userEvent.setup();
    render(<Chat onUnauthorized={() => {}} csrfToken="csrf-1" />);
    await screen.findByText("Hola.");

    fireEvent.change(screen.getByLabelText("Archivos para adjuntar"), {
      target: {
        files: [
          new File(["uno"], "bien.txt", { type: "text/plain" }),
          new File(["MZ"], "malo.exe", { type: "application/octet-stream" }),
        ],
      },
    });

    expect(await screen.findByText(/Extensión no permitida/)).toBeTruthy();
    expect(screen.getByText("malo.exe")).toBeTruthy();

    await user.click(screen.getByLabelText("Enviar mensaje"));

    await waitFor(() =>
      expect(enviado).toContain(
        "Adjunto en la NAS: files/Entrada/Documentos/bien.txt",
      ),
    );
    expect(enviado).not.toContain("malo.exe");
    // El chip fallido sigue ahí para poder quitarlo.
    expect(screen.getByText("malo.exe")).toBeTruthy();
  });

  it("muestra el error del servidor y deja el chip marcado", async () => {
    apiMock.uploadFile.mockRejectedValue(
      new ApiError(413, "El archivo supera el límite de 50 MB"),
    );

    render(<Chat onUnauthorized={() => {}} csrfToken="csrf-1" />);
    await screen.findByText("Hola.");

    const input = screen.getByLabelText("Archivos para adjuntar");
    fireEvent.change(input, {
      target: { files: [new File(["x"], "grande.bin")] },
    });

    expect(
      await screen.findByText(/El archivo supera el límite de 50 MB/),
    ).toBeTruthy();
    expect(screen.getByText("grande.bin")).toBeTruthy();
  });

  it("rechaza adjuntos de más de 50 MB sin llamar al servidor", async () => {
    render(<Chat onUnauthorized={() => {}} csrfToken="csrf-1" />);
    await screen.findByText("Hola.");

    const grande = new File(["x"], "enorme.bin");
    Object.defineProperty(grande, "size", { value: 51 * 1024 * 1024 });
    fireEvent.change(screen.getByLabelText("Archivos para adjuntar"), {
      target: { files: [grande] },
    });

    expect(await screen.findByText("Máximo 50 MB por archivo")).toBeTruthy();
    expect(apiMock.uploadFile).not.toHaveBeenCalled();
  });

  it("usa el límite configurado en Ajustes → NAS", async () => {
    apiMock.nasConfig.mockResolvedValue({ upload_max_mb: 5 });
    render(<Chat onUnauthorized={() => {}} csrfToken="csrf-1" />);
    await screen.findByText("Hola.");
    // Deja resolver la consulta de configuración antes de elegir el archivo.
    await waitFor(() =>
      expect(apiMock.nasConfig).toHaveBeenCalled(),
    );

    const grande = new File(["x"], "seis.bin");
    Object.defineProperty(grande, "size", { value: 6 * 1024 * 1024 });
    fireEvent.change(screen.getByLabelText("Archivos para adjuntar"), {
      target: { files: [grande] },
    });

    expect(
      await screen.findByText("Máximo 5 MB por archivo"),
    ).toBeTruthy();
    expect(apiMock.uploadFile).not.toHaveBeenCalled();
  });

  it("limita a 10 archivos por mensaje", async () => {
    apiMock.uploadFile.mockImplementation((file: File) =>
      Promise.resolve({
        ok: true,
        path: `files/Entrada/Documentos/${file.name}`,
        name: file.name,
        size: file.size,
      }),
    );

    render(<Chat onUnauthorized={() => {}} csrfToken="csrf-1" />);
    await screen.findByText("Hola.");

    const archivos = Array.from(
      { length: 11 },
      (_, i) => new File(["x"], `f${i}.txt`, { type: "text/plain" }),
    );
    fireEvent.change(screen.getByLabelText("Archivos para adjuntar"), {
      target: { files: archivos },
    });

    expect(
      await screen.findByText("Máximo 10 archivos por mensaje"),
    ).toBeTruthy();
    await waitFor(() => expect(apiMock.uploadFile).toHaveBeenCalledTimes(10));
  });
});
