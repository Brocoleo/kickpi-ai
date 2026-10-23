import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
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
  nasStatus: vi.fn(),
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
    apiMock.nasStatus.mockReset();
    apiMock.deleteNasFile.mockReset();
    apiMock.nasStatus.mockResolvedValue({
      inbox: "files/Entrada",
      ready: true,
      files: 1,
    });
    apiMock.deleteNasFile.mockResolvedValue({
      ok: true,
      path: "files/Entrada/Fotos/foto.jpg",
    });
    apiMock.nasRecent.mockResolvedValue({
      ready: true,
      files: [
        {
          name: "foto.jpg",
          path: "files/Entrada/Fotos/foto.jpg",
          size: 2048,
          modified: "2026-10-20T12:00:00+00:00",
          deletable: true,
          folder: "Fotos",
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
                deletable: false,
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
      "files/Entrada/Fotos/foto.jpg",
      "csrf-123",
    );
    // Desaparece al instante: no espera al refresco (lento) del índice.
    await waitFor(() =>
      expect(screen.queryByText("foto.jpg")).not.toBeInTheDocument(),
    );
    // Y un refresco que todavía devuelva el archivo no lo resucita.
    await waitFor(() => expect(apiMock.nasRecent).toHaveBeenCalledTimes(2));
    expect(screen.queryByText("foto.jpg")).not.toBeInTheDocument();
  });

  it("muestra la carpeta de clasificación en cada archivo", async () => {
    render(<Files csrfToken={null} onUnauthorized={() => {}} />);

    await screen.findByText("foto.jpg");
    expect(screen.getByText(/Fotos · 2 KB/)).toBeInTheDocument();
  });

  it("lleva a las carpetas de Entrada desde Recientes", async () => {
    render(<Files csrfToken={null} onUnauthorized={() => {}} />);

    await screen.findByText("foto.jpg");
    await userEvent.click(
      screen.getByRole("button", { name: "Ver carpetas de Entrada" }),
    );

    await waitFor(() =>
      expect(apiMock.nasList).toHaveBeenCalledWith("files/Entrada"),
    );
    expect(screen.getByRole("tab", { name: "Explorar" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });

  it("no ofrece borrar archivos fuera de Entrada", async () => {
    render(<Files csrfToken={null} onUnauthorized={() => {}} />);

    await screen.findByText("foto.jpg");
    await userEvent.click(screen.getByRole("tab", { name: "Explorar" }));
    await userEvent.click(await screen.findByRole("button", { name: "Documentos" }));
    await screen.findByText("carta.pdf");

    expect(
      screen.queryByRole("button", { name: "Borrar carta.pdf" }),
    ).not.toBeInTheDocument();
  });

  it("borra desde el visor y lo cierra", async () => {
    render(<Files csrfToken="csrf-123" onUnauthorized={() => {}} />);

    await screen.findByText("foto.jpg");
    await userEvent.click(screen.getByRole("button", { name: /^foto\.jpg/ }));

    const dialogo = await screen.findByRole("dialog", { name: "foto.jpg" });
    await userEvent.click(
      within(dialogo).getByRole("button", { name: "Borrar foto.jpg" }),
    );
    await userEvent.click(
      within(dialogo).getByRole("button", { name: "Borrar" }),
    );

    expect(apiMock.deleteNasFile).toHaveBeenCalledWith(
      "files/Entrada/Fotos/foto.jpg",
      "csrf-123",
    );
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
  });
});
