import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { api } from "./api";

/**
 * `uploadBatch` usa XMLHttpRequest (y no fetch) para poder informar del
 * avance real de los bytes. Estas pruebas fijan el contrato: URL, CSRF,
 * progreso, cuerpo JSON y traducción de errores (incluido Retry-After).
 */

class FakeXHR {
  static ultima: FakeXHR | null = null;

  method = "";
  url = "";
  headers: Record<string, string> = {};
  withCredentials = false;
  responseType = "";
  status = 0;
  statusText = "";
  responseText = "";
  upload: { onprogress: ((evento: ProgressEvent) => void) | null } = {
    onprogress: null,
  };
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onabort: (() => void) | null = null;

  private cabecerasRespuesta: Record<string, string> = {};

  constructor() {
    FakeXHR.ultima = this;
  }

  open(method: string, url: string): void {
    this.method = method;
    this.url = url;
  }

  setRequestHeader(clave: string, valor: string): void {
    this.headers[clave] = valor;
  }

  getResponseHeader(clave: string): string | null {
    return this.cabecerasRespuesta[clave.toLowerCase()] ?? null;
  }

  send(_cuerpo: FormData): void {
    /* la prueba decide cuándo y cómo responde */
  }

  /** Simula la respuesta del servidor. */
  responder(
    status: number,
    cuerpo: string,
    cabeceras: Record<string, string> = {},
    statusText = "",
  ): void {
    this.status = status;
    this.statusText = statusText;
    this.responseText = cuerpo;
    this.cabecerasRespuesta = Object.fromEntries(
      Object.entries(cabeceras).map(([clave, valor]) => [
        clave.toLowerCase(),
        valor,
      ]),
    );
    this.onload?.();
  }
}

const XHROriginal = globalThis.XMLHttpRequest;

beforeEach(() => {
  FakeXHR.ultima = null;
  globalThis.XMLHttpRequest = FakeXHR as unknown as typeof XMLHttpRequest;
});

afterEach(() => {
  globalThis.XMLHttpRequest = XHROriginal;
});

const lote = () =>
  new File(["contenido"], "foto.jpg", { type: "image/jpeg" });

describe("api.uploadBatch", () => {
  it("manda el lote a la ruta correcta con CSRF y resuelve el JSON", async () => {
    const promesa = api.uploadBatch([lote()], "csrf-1");
    const xhr = FakeXHR.ultima!;
    expect(xhr.method).toBe("POST");
    expect(xhr.url).toBe("/api/files/upload-batch");
    expect(xhr.headers["X-CSRF-Token"]).toBe("csrf-1");

    xhr.responder(
      200,
      JSON.stringify({
        ok: true,
        results: [
          {
            ok: true,
            path: "files/Entrada/Fotos/a.jpg",
            name: "foto.jpg",
            size: 9,
            folder: "Fotos",
          },
        ],
        uploaded: 1,
        failed: 0,
      }),
    );

    await expect(promesa).resolves.toMatchObject({ ok: true, uploaded: 1 });
  });

  it("informa del avance de bytes (solo si es computable)", async () => {
    const avances: Array<[number, number]> = [];
    const promesa = api.uploadBatch([lote()], null, (cargado, total) =>
      avances.push([cargado, total]),
    );
    const xhr = FakeXHR.ultima!;

    xhr.upload.onprogress?.({
      lengthComputable: true,
      loaded: 512,
      total: 1024,
    } as ProgressEvent);
    // Evento sin tamaño conocido: no se inventa una barra.
    xhr.upload.onprogress?.({
      lengthComputable: false,
      loaded: 0,
      total: 0,
    } as ProgressEvent);

    expect(avances).toEqual([[512, 1024]]);

    xhr.responder(200, JSON.stringify({ ok: true, results: [] }));
    await promesa;
  });

  it("traduce un 429 con Retry-After a ApiError", async () => {
    const promesa = api.uploadBatch([lote()], null);
    FakeXHR.ultima!.responder(
      429,
      JSON.stringify({ detail: "Demasiadas subidas seguidas" }),
      { "Retry-After": "42" },
    );

    await expect(promesa).rejects.toMatchObject({
      status: 429,
      message: "Demasiadas subidas seguidas",
      retryAfter: 42,
    });
  });

  it("si la respuesta no es JSON usa el statusText", async () => {
    const promesa = api.uploadBatch([lote()], null);
    FakeXHR.ultima!.responder(502, "<html>Bad Gateway</html>", {}, "Bad Gateway");

    await expect(promesa).rejects.toMatchObject({
      status: 502,
      message: "Bad Gateway",
    });
  });

  it("un fallo de red no deja la promesa colgada", async () => {
    const promesa = api.uploadBatch([lote()], null);
    FakeXHR.ultima!.onerror?.();

    await expect(promesa).rejects.toMatchObject({
      status: 0,
      message: expect.stringContaining("No se pudo conectar"),
    });
  });
});
