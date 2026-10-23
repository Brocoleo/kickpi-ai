import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Chat } from "./Chat";
import {
  ApiError,
  type ChatStreamHandlers,
  type UploadBatchResult,
  type UploadResult,
} from "../api";

/**
 * Adjuntos del chat: se suben a Entrada/ del NAS en lotes y las rutas viajan
 * en el mensaje para que el modelo no tenga que buscarlas a ciegas.
 */

const apiMock = vi.hoisted(() => ({
  nasConfig: vi.fn(),
  conversations: vi.fn(),
  conversation: vi.fn(),
  chatStream: vi.fn(),
  stopChat: vi.fn(),
  deleteConversation: vi.fn(),
  uploadFile: vi.fn(),
  uploadBatch: vi.fn(),
}));

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return {
    ...actual,
    api: apiMock,
    ApiError: actual.ApiError,
  };
});

/** Resultado de un archivo guardado (lo que devuelve el endpoint batch). */
function guardado(nombre: string, carpeta = "Documentos"): UploadResult {
  return {
    ok: true,
    path: `files/Entrada/${carpeta}/${nombre}`,
    name: nombre,
    size: 4,
    folder: carpeta,
  };
}

/** Lote completo en verde, en el orden en que se enviaron los archivos. */
function loteOk(...items: UploadResult[]): UploadBatchResult {
  return {
    ok: true,
    results: items,
    uploaded: items.length,
    failed: 0,
  };
}

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
  it("sube el archivo por lote y menciona la ruta en el mensaje", async () => {
    apiMock.uploadBatch.mockImplementation((files: File[]) =>
      Promise.resolve(loteOk(...files.map((f) => guardado(f.name)))),
    );
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
    const { container } = render(
      <Chat onUnauthorized={() => {}} csrfToken="csrf-1" />,
    );
    await screen.findByText("Hola.");

    const input = screen.getByLabelText("Archivos para adjuntar");
    const archivo = new File(["%PDF"], "factura.pdf", {
      type: "application/pdf",
    });
    fireEvent.change(input, { target: { files: [archivo] } });

    await screen.findByText("factura.pdf");
    expect(apiMock.uploadBatch).toHaveBeenCalledWith([archivo], "csrf-1");

    // Con adjunto se puede enviar aunque no haya texto.
    const enviar = screen.getByLabelText("Enviar mensaje") as HTMLButtonElement;
    expect(enviar.disabled).toBe(false);

    await user.type(screen.getByPlaceholderText("Escribe a Jarvis…"), "mira esto");
    await user.click(enviar);

    await waitFor(() =>
      expect(enviado).toContain(
        "Adjunto en la NAS: files/Entrada/Documentos/factura.pdf",
      ),
    );
    expect(enviado).toContain("mira esto");
    // El chip se limpia al enviar.
    await waitFor(() => expect(container.querySelector(".attach-chip")).toBeNull());
  });

  it("manda los archivos de un lote en UNA sola petición", async () => {
    const rutas = [
      guardado("a.jpg", "Fotos"),
      guardado("b.mov", "Videos"),
      guardado("c.mp3", "Audio"),
    ];
    apiMock.uploadBatch.mockResolvedValue(loteOk(...rutas));
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

    const archivos = rutas.map(
      (r) => new File(["x"], r.name, { type: "application/octet-stream" }),
    );
    fireEvent.change(screen.getByLabelText("Archivos para adjuntar"), {
      target: { files: archivos },
    });

    await screen.findByText("a.jpg");
    await screen.findByText("b.mov");
    await screen.findByText("c.mp3");
    // Un solo request para los tres (BATCH_SIZE = 10).
    await waitFor(() => expect(apiMock.uploadBatch).toHaveBeenCalledTimes(1));
    expect(apiMock.uploadBatch.mock.calls[0][0]).toHaveLength(3);
    expect(apiMock.uploadFile).not.toHaveBeenCalled();

    const enviar = screen.getByLabelText("Enviar mensaje") as HTMLButtonElement;
    await waitFor(() => expect(enviar.disabled).toBe(false));
    await user.click(enviar);

    await waitFor(() => {
      for (const r of rutas) {
        expect(enviado).toContain(`Adjunto en la NAS: ${r.path}`);
      }
    });
  });

  it("100 archivos se mandan en 10 lotes de 10", async () => {
    apiMock.uploadBatch.mockImplementation((files: File[]) =>
      Promise.resolve(loteOk(...files.map((f) => guardado(f.name)))),
    );

    render(<Chat onUnauthorized={() => {}} csrfToken="csrf-1" />);
    await screen.findByText("Hola.");

    const archivos = Array.from(
      { length: 100 },
      (_, i) => new File(["x"], `f${i}.txt`, { type: "text/plain" }),
    );
    fireEvent.change(screen.getByLabelText("Archivos para adjuntar"), {
      target: { files: archivos },
    });

    await waitFor(
      () => expect(apiMock.uploadBatch).toHaveBeenCalledTimes(10),
      { timeout: 20_000 },
    );
    for (const llamada of apiMock.uploadBatch.mock.calls) {
      expect(llamada[0]).toHaveLength(10);
    }
    // Sin aviso de tope: 100 es exactamente el máximo permitido.
    expect(screen.queryByText(/Máximo 100 archivos por mensaje/)).toBeNull();
  });

  it("muestra el lote en curso y avanza la barra", async () => {
    // Cada lote queda pendiente hasta que la prueba lo resuelve: así se puede
    // observar el estado intermedio (lote 1 de 2, 0 de 15).
    const pendientes: Array<() => void> = [];
    apiMock.uploadBatch.mockImplementation(
      (files: File[]) =>
        new Promise<UploadBatchResult>((resolve) => {
          pendientes.push(() =>
            resolve(loteOk(...files.map((f) => guardado(f.name)))),
          );
        }),
    );

    const { container } = render(
      <Chat onUnauthorized={() => {}} csrfToken="csrf-1" />,
    );
    await screen.findByText("Hola.");

    const archivos = Array.from(
      { length: 15 },
      (_, i) => new File(["x"], `f${i}.txt`, { type: "text/plain" }),
    );
    fireEvent.change(screen.getByLabelText("Archivos para adjuntar"), {
      target: { files: archivos },
    });

    await waitFor(() => expect(pendientes).toHaveLength(1));
    const progreso = container.querySelector(".attach-progress");
    expect(progreso?.textContent).toContain("Lote 1 de 2");

    pendientes[0]();
    await waitFor(() => expect(pendientes).toHaveLength(2));
    expect(container.querySelector(".attach-progress")?.textContent).toContain(
      "Lote 2 de 2 · 10/15",
    );

    pendientes[1]();
    await waitFor(() =>
      expect(container.querySelector(".attach-progress")).toBeNull(),
    );
  });

  it("si un archivo del lote falla, los demás se envían y el fallido se queda marcado", async () => {
    apiMock.uploadBatch.mockResolvedValue({
      ok: false,
      results: [
        guardado("bien.txt"),
        { ok: false, name: "malo.exe", error: "Extensión no permitida" },
      ],
      uploaded: 1,
      failed: 1,
    });
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

  it("un error del lote deja los chips pendientes marcados", async () => {
    apiMock.uploadBatch.mockRejectedValue(
      new ApiError(413, "El archivo supera el límite de 50 MB"),
    );

    render(<Chat onUnauthorized={() => {}} csrfToken="csrf-1" />);
    await screen.findByText("Hola.");

    fireEvent.change(screen.getByLabelText("Archivos para adjuntar"), {
      target: {
        files: [new File(["x"], "grande.bin")],
      },
    });

    expect(
      await screen.findByText(/El archivo supera el límite de 50 MB/),
    ).toBeTruthy();
    expect(screen.getByText("grande.bin")).toBeTruthy();
  });

  it("reintenta el lote cuando el servidor pide esperar (429)", async () => {
    apiMock.uploadBatch
      .mockRejectedValueOnce(new ApiError(429, "Demasiadas subidas", 0.01))
      .mockResolvedValueOnce(loteOk(guardado("reintento.txt")));

    render(<Chat onUnauthorized={() => {}} csrfToken="csrf-1" />);
    await screen.findByText("Hola.");

    fireEvent.change(screen.getByLabelText("Archivos para adjuntar"), {
      target: { files: [new File(["x"], "reintento.txt", { type: "text/plain" })] },
    });

    await waitFor(() => expect(apiMock.uploadBatch).toHaveBeenCalledTimes(2));
    await waitFor(() =>
      expect(screen.getByLabelText("Enviar mensaje")).toHaveProperty(
        "disabled",
        false,
      ),
    );
    expect(screen.queryByText(/Demasiadas subidas/)).toBeNull();
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
    expect(apiMock.uploadBatch).not.toHaveBeenCalled();
  });

  it("usa el límite configurado en Ajustes → NAS", async () => {
    apiMock.nasConfig.mockResolvedValue({ upload_max_mb: 5 });
    render(<Chat onUnauthorized={() => {}} csrfToken="csrf-1" />);
    await screen.findByText("Hola.");
    // Deja resolver la consulta de configuración antes de elegir el archivo.
    await waitFor(() => expect(apiMock.nasConfig).toHaveBeenCalled());

    const grande = new File(["x"], "seis.bin");
    Object.defineProperty(grande, "size", { value: 6 * 1024 * 1024 });
    fireEvent.change(screen.getByLabelText("Archivos para adjuntar"), {
      target: { files: [grande] },
    });

    expect(await screen.findByText("Máximo 5 MB por archivo")).toBeTruthy();
    expect(apiMock.uploadBatch).not.toHaveBeenCalled();
  });

  it("ajusta el tamaño del lote al límite por minuto configurado", async () => {
    // Con 4 subidas/minuto, mandar 10 de golpe daría 413 («no cabe»).
    apiMock.nasConfig.mockResolvedValue({
      inbox: "files/Entrada",
      inbox_dir: "/mnt/data/nas/files/Entrada",
      upload_max_mb: 50,
      upload_rate_per_min: 4,
      classifier_rules: null,
    });
    apiMock.uploadBatch.mockImplementation((files: File[]) =>
      Promise.resolve(loteOk(...files.map((f) => guardado(f.name)))),
    );

    render(<Chat onUnauthorized={() => {}} csrfToken="csrf-1" />);
    await screen.findByText("Hola.");
    await waitFor(() => expect(apiMock.nasConfig).toHaveBeenCalled());

    const archivos = Array.from(
      { length: 12 },
      (_, i) => new File(["x"], `f${i}.txt`, { type: "text/plain" }),
    );
    fireEvent.change(screen.getByLabelText("Archivos para adjuntar"), {
      target: { files: archivos },
    });

    await waitFor(() => expect(apiMock.uploadBatch).toHaveBeenCalledTimes(3));
    for (const llamada of apiMock.uploadBatch.mock.calls) {
      expect(llamada[0]).toHaveLength(4);
    }
  });

  it("no manda lotes de más de 450 MB (aunque quepan 10 archivos)", async () => {
    apiMock.nasConfig.mockResolvedValue({
      inbox: "files/Entrada",
      inbox_dir: "/mnt/data/nas/files/Entrada",
      upload_max_mb: 500,
      upload_rate_per_min: 60,
      classifier_rules: null,
    });
    apiMock.uploadBatch.mockImplementation((files: File[]) =>
      Promise.resolve(loteOk(...files.map((f) => guardado(f.name)))),
    );

    render(<Chat onUnauthorized={() => {}} csrfToken="csrf-1" />);
    await screen.findByText("Hola.");
    await waitFor(() => expect(apiMock.nasConfig).toHaveBeenCalled());

    const archivos = [0, 1, 2].map((i) => {
      const file = new File(["x"], `video${i}.mp4`, { type: "video/mp4" });
      Object.defineProperty(file, "size", { value: 300 * 1024 * 1024 });
      return file;
    });
    fireEvent.change(screen.getByLabelText("Archivos para adjuntar"), {
      target: { files: archivos },
    });

    await waitFor(() => expect(apiMock.uploadBatch).toHaveBeenCalledTimes(3));
    for (const llamada of apiMock.uploadBatch.mock.calls) {
      expect(llamada[0]).toHaveLength(1);
    }
  });

  it("limita a 100 archivos por mensaje", async () => {
    apiMock.uploadBatch.mockImplementation((files: File[]) =>
      Promise.resolve(loteOk(...files.map((f) => guardado(f.name)))),
    );

    render(<Chat onUnauthorized={() => {}} csrfToken="csrf-1" />);
    await screen.findByText("Hola.");

    const archivos = Array.from(
      { length: 101 },
      (_, i) => new File(["x"], `f${i}.txt`, { type: "text/plain" }),
    );
    fireEvent.change(screen.getByLabelText("Archivos para adjuntar"), {
      target: { files: archivos },
    });

    expect(
      await screen.findByText("Máximo 100 archivos por mensaje"),
    ).toBeTruthy();
    await waitFor(
      () => expect(apiMock.uploadBatch).toHaveBeenCalledTimes(10),
      { timeout: 20_000 },
    );
    const enviados = apiMock.uploadBatch.mock.calls.flatMap((c) => c[0]);
    expect(enviados).toHaveLength(100);
  });
});
