import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Files } from "./Files";

/**
 * La pestaña Archivos ya no es solo un iframe: tiene navegación nativa.
 * Lo que se protege aquí es que las dos fuentes (recientes y listado) se
 * pidan al montar y que entrar en una carpeta pida esa carpeta, no la raíz.
 */
const apiMock = vi.hoisted(() => ({
  nasRecent: vi.fn(),
  nasList: vi.fn(),
  nasSearch: vi.fn(),
  deleteNasFile: vi.fn(),
  nasFileUrl: vi.fn(
    (path: string) => `/api/nas/file?path=${encodeURIComponent(path)}`,
  ),
}));

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return { ...actual, api: apiMock };
});

const ROOT = {
  ready: true,
  path: "",
  parent: "",
  dirs: [{ name: "Documentos", path: "Documentos" }],
  files: [],
  error: null,
};

describe("Files · panel NAS nativo", () => {
  beforeEach(() => {
    apiMock.nasRecent.mockReset();
    apiMock.nasList.mockReset();
    apiMock.nasSearch.mockReset();
    apiMock.deleteNasFile.mockReset();
    apiMock.deleteNasFile.mockResolvedValue({ ok: true, path: "Entrada/foto.jpg" });
    apiMock.nasRecent.mockResolvedValue({
      ready: true,
      files: [
        {
          name: "foto.jpg",
          path: "Entrada/foto.jpg",
          size: 2048,
          modified: "2026-10-20T12:00:00+00:00",
        },
      ],
      error: null,
    });
    apiMock.nasList.mockImplementation(async (path: string) =>
      path === "Documentos"
        ? {
            ready: true,
            path: "Documentos",
            parent: "",
            dirs: [],
            files: [
              {
                name: "carta.pdf",
                path: "Documentos/carta.pdf",
                size: 1024,
                modified: "2026-10-19T12:00:00+00:00",
              },
            ],
            error: null,
          }
        : ROOT,
    );
  });

  it("muestra recientes y entra en una carpeta", async () => {
    render(<Files csrfToken={null} onUnauthorized={() => {}} />);

    expect(await screen.findByText("foto.jpg")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: "Explorar" }));
    const carpeta = await screen.findByRole("button", { name: "Documentos" });
    await userEvent.click(carpeta);

    expect(await screen.findByText("carta.pdf")).toBeInTheDocument();
    expect(apiMock.nasList).toHaveBeenCalledWith("Documentos");
    // La miga de pan permite volver a la raíz.
    expect(screen.getByRole("button", { name: "NAS" })).toBeInTheDocument();
  });

  it("busca y muestra el fragmento de contenido", async () => {
    apiMock.nasSearch.mockResolvedValue({
      ready: true,
      query: "router",
      total_matches: 1,
      results: [
        {
          name: "notas.md",
          path: "notas/notas.md",
          size: 120,
          modified: "2026-10-18T12:00:00+00:00",
          match: "contenido",
          snippet: "…hablamos del router y de la factura…",
        },
      ],
      error: null,
    });
    render(<Files csrfToken={null} onUnauthorized={() => {}} />);

    await screen.findByText("foto.jpg");
    const caja = screen.getByRole("searchbox", { name: "Buscar en el NAS" });
    await userEvent.type(caja, "router{Enter}");

    expect(await screen.findByText("notas.md")).toBeInTheDocument();
    expect(
      screen.getByText("…hablamos del router y de la factura…"),
    ).toBeInTheDocument();
    expect(apiMock.nasSearch).toHaveBeenCalledWith("router");
  });

  it("borra un adjunto de Entrada solo tras confirmar", async () => {
    render(<Files csrfToken="csrf-123" onUnauthorized={() => {}} />);

    await screen.findByText("foto.jpg");
    await userEvent.click(
      screen.getByRole("button", { name: "Borrar foto.jpg" }),
    );
    expect(apiMock.deleteNasFile).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole("button", { name: "Borrar" }));
    expect(apiMock.deleteNasFile).toHaveBeenCalledWith(
      "Entrada/foto.jpg",
      "csrf-123",
    );
  });
});
