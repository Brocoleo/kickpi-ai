import { useCallback, useEffect, useState } from "react";
import type { FormEvent } from "react";
import type { NasEntry, NasListing, NasSearchResult } from "../types";
import { api, ApiError } from "../api";
import {
  ExternalIcon,
  FileIcon,
  FolderIcon,
  RefreshIcon,
  SearchIcon,
  ShieldIcon,
  TrashIcon,
  XIcon,
} from "./icons";

/**
 * Archivos del NAS.
 *
 * La vista principal es NATIVA: explora carpetas, busca y muestra miniaturas
 * usando `/api/nas/*` del agente (solo lectura). FileBrowser sigue disponible
 * como vista avanzada, en un iframe del mismo origen: sirve para renombrar,
 * ordenar o descargar en lote sin duplicar aquí esas funciones.
 *
 * El iframe se monta una sola vez y se oculta al cambiar de vista: volver a
 * él no recarga la app ni pierde la carpeta en la que estabas.
 */
const PORTAINER_PORT = 9443;

const IMAGE_EXT = new Set([".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".bmp"]);

type Vista = "recientes" | "explorar" | "buscar" | "filebrowser";

function extension(nombre: string): string {
  const punto = nombre.lastIndexOf(".");
  return punto >= 0 ? nombre.slice(punto).toLowerCase() : "";
}

function esImagen(item: NasEntry): boolean {
  return IMAGE_EXT.has(item.ext || extension(item.name));
}

function formatoTamano(bytes?: number): string {
  if (bytes === undefined) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${(bytes / 1024 / 1024 / 1024).toFixed(1)} GB`;
}

function fecha(iso?: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleDateString("es-CL", { day: "2-digit", month: "short" });
}

function portainerUrl() {
  const host = window.location.hostname;
  const base =
    host === "localhost" || host === "127.0.0.1"
      ? "kickpi-jarvis.tail1e3b4d.ts.net"
      : host;
  return `https://${base}:${PORTAINER_PORT}/`;
}

function mensajeError(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return "No se pudo leer el NAS";
}

function Archivo({
  item,
  onAbrir,
  onBorrar,
  confirmando,
  onConfirmar,
  onCancelar,
}: {
  item: NasEntry;
  onAbrir: (item: NasEntry) => void;
  onBorrar?: () => void;
  confirmando?: boolean;
  onConfirmar?: () => void;
  onCancelar?: () => void;
}) {
  const imagen = esImagen(item);
  return (
    <div className="nas-file">
      <button
        type="button"
        className="nas-file-main"
        onClick={() => onAbrir(item)}
      >
        {imagen ? (
          <img
            className="nas-thumb"
            src={api.nasFileUrl(item.path)}
            alt=""
            loading="lazy"
            decoding="async"
          />
        ) : (
          <span className="nas-file-icon" aria-hidden="true">
            <FileIcon />
          </span>
        )}
        <span className="nas-file-text">
          <span className="nas-file-name">{item.name}</span>
          <span className="nas-file-meta">
            {[formatoTamano(item.size), fecha(item.modified)]
              .filter(Boolean)
              .join(" · ")}
          </span>
        </span>
      </button>
      <div className="nas-file-actions">
        {onBorrar && !confirmando && (
          <button
            type="button"
            className="icon-btn nas-download"
            aria-label={`Borrar ${item.name}`}
            title="Borrar de Entrada"
            onClick={onBorrar}
          >
            <TrashIcon />
          </button>
        )}
        {confirmando && (
          <>
            <button
              type="button"
              className="ghost-btn nas-danger"
              onClick={onConfirmar}
            >
              Borrar
            </button>
            <button
              type="button"
              className="ghost-btn"
              onClick={onCancelar}
            >
              No
            </button>
          </>
        )}
        <a
          className="icon-btn nas-download"
          href={api.nasFileUrl(item.path, true)}
          download={item.name}
          aria-label={`Descargar ${item.name}`}
          title="Descargar"
        >
          <ExternalIcon />
        </a>
      </div>
    </div>
  );
}

export function Files({
  csrfToken,
  onUnauthorized,
}: {
  csrfToken: string | null;
  onUnauthorized: () => void;
}) {
  const [vista, setVista] = useState<Vista>("recientes");
  const [recientes, setRecientes] = useState<NasEntry[]>([]);
  const [listado, setListado] = useState<NasListing | null>(null);
  const [ruta, setRuta] = useState("");
  const [busqueda, setBusqueda] = useState("");
  const [resultados, setResultados] = useState<NasSearchResult | null>(null);
  const [cargando, setCargando] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [visor, setVisor] = useState<NasEntry | null>(null);
  const [confirmarBorrado, setConfirmarBorrado] = useState<string | null>(null);
  const [frameListo, setFrameListo] = useState(false);
  const portainer = portainerUrl();

  const gestionarError = useCallback(
    (err: unknown) => {
      if (err instanceof ApiError && err.status === 401) onUnauthorized();
      setError(mensajeError(err));
    },
    [onUnauthorized],
  );

  const cargarRecientes = useCallback(async () => {
    setCargando(true);
    setError(null);
    try {
      const data = await api.nasRecent(30);
      setRecientes(Array.isArray(data.files) ? data.files : []);
      if (data.error) setError(data.error);
    } catch (err) {
      gestionarError(err);
    } finally {
      setCargando(false);
    }
  }, [gestionarError]);

  const cargarCarpeta = useCallback(async (nuevaRuta: string) => {
    setCargando(true);
    setError(null);
    try {
      const data = await api.nasList(nuevaRuta);
      setListado(data);
      setRuta(data.path || nuevaRuta);
      if (data.error) setError(data.error);
    } catch (err) {
      gestionarError(err);
    } finally {
      setCargando(false);
    }
  }, [gestionarError]);

  const irAExplorar = useCallback(
    (nuevaRuta: string) => {
      setVista("explorar");
      void cargarCarpeta(nuevaRuta);
    },
    [cargarCarpeta],
  );

  useEffect(() => {
    void cargarRecientes();
    void cargarCarpeta("");
  }, [cargarRecientes, cargarCarpeta]);

  useEffect(() => {
    if (!visor) return;
    const alPulsar = (event: KeyboardEvent) => {
      if (event.key === "Escape") setVisor(null);
    };
    window.addEventListener("keydown", alPulsar);
    return () => window.removeEventListener("keydown", alPulsar);
  }, [visor]);

  const buscar = useCallback(
    async (event: FormEvent) => {
      event.preventDefault();
      const q = busqueda.trim();
      if (!q) return;
      setVista("buscar");
      setCargando(true);
      setError(null);
      try {
        setResultados(await api.nasSearch(q));
      } catch (err) {
        gestionarError(err);
      } finally {
        setCargando(false);
      }
    },
    [busqueda, gestionarError],
  );

  const abrir = useCallback((item: NasEntry) => {
    if (esImagen(item)) {
      setVisor(item);
      return;
    }
    if (extension(item.name) === ".pdf") {
      window.open(api.nasFileUrl(item.path), "_blank", "noopener");
      return;
    }
    const enlace = document.createElement("a");
    enlace.href = api.nasFileUrl(item.path, true);
    enlace.download = item.name;
    enlace.click();
  }, []);

  const borrar = useCallback(
    async (item: NasEntry) => {
      setError(null);
      try {
        await api.deleteNasFile(item.path, csrfToken);
        setConfirmarBorrado(null);
        if (vista === "buscar" && resultados) {
          setResultados(await api.nasSearch(resultados.query));
        } else if (vista === "explorar") {
          await cargarCarpeta(ruta);
        }
        await cargarRecientes();
      } catch (err) {
        gestionarError(err);
      }
    },
    [
      csrfToken,
      vista,
      resultados,
      ruta,
      cargarCarpeta,
      cargarRecientes,
      gestionarError,
    ],
  );

  const refrescar = useCallback(() => {
    if (vista === "recientes") void cargarRecientes();
    else if (vista === "explorar") void cargarCarpeta(ruta);
    else if (vista === "buscar" && resultados) {
      void api
        .nasSearch(resultados.query)
        .then(setResultados)
        .catch(gestionarError);
    }
  }, [vista, ruta, resultados, cargarRecientes, cargarCarpeta, gestionarError]);

  const abrirFileBrowser = useCallback(() => {
    setFrameListo(true);
    setVista("filebrowser");
  }, []);

  const partes = ruta ? ruta.split("/") : [];
  const listadoVacio =
    vista === "explorar" && listado && !listado.dirs.length && !listado.files.length;
  const archivosVista =
    vista === "buscar" ? resultados?.results ?? [] : recientes;

  // Borrar solo se ofrece para adjuntos propios (Entrada/). El backend lo
  // vuelve a comprobar: la UI no es la frontera de seguridad.
  const acciones = (item: NasEntry) =>
    item.path.startsWith("Entrada/")
      ? {
          onBorrar: () => setConfirmarBorrado(item.path),
          confirmando: confirmarBorrado === item.path,
          onConfirmar: () => void borrar(item),
          onCancelar: () => setConfirmarBorrado(null),
        }
      : {};

  return (
    <div className="screen files-screen">
      <div className="screen-head">
        <h1 className="screen-title">Archivos</h1>
        <div className="head-meta">
          <button
            type="button"
            className="icon-btn"
            onClick={refrescar}
            aria-label="Actualizar"
            title="Actualizar"
          >
            <RefreshIcon />
          </button>
          <a
            className="icon-btn"
            href="/files/"
            target="_blank"
            rel="noopener noreferrer"
            aria-label="Abrir FileBrowser en otra pestaña"
            title="Abrir en FileBrowser"
          >
            <ExternalIcon />
          </a>
        </div>
      </div>

      <form className="nas-search" onSubmit={(event) => void buscar(event)}>
        <SearchIcon className="nas-search-icon" />
        <input
          type="search"
          value={busqueda}
          onChange={(event) => setBusqueda(event.target.value)}
          placeholder="Buscar por nombre o contenido…"
          aria-label="Buscar en el NAS"
          enterKeyHint="search"
        />
        {busqueda && (
          <button
            type="button"
            className="icon-btn"
            aria-label="Limpiar búsqueda"
            onClick={() => {
              setBusqueda("");
              setResultados(null);
              setVista("recientes");
            }}
          >
            <XIcon />
          </button>
        )}
      </form>

      <div className="nas-tabs" role="tablist" aria-label="Vistas de archivos">
        <button
          type="button"
          role="tab"
          aria-selected={vista === "recientes"}
          className={`nas-tab${vista === "recientes" ? " active" : ""}`}
          onClick={() => setVista("recientes")}
        >
          Recientes
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={vista === "explorar"}
          className={`nas-tab${vista === "explorar" ? " active" : ""}`}
          onClick={() => irAExplorar(ruta)}
        >
          Explorar
        </button>
        {vista === "buscar" && (
          <span className="nas-tab active" role="tab" aria-selected="true">
            Búsqueda
          </span>
        )}
        <button
          type="button"
          role="tab"
          aria-selected={vista === "filebrowser"}
          className={`nas-tab nas-tab-avanzado${
            vista === "filebrowser" ? " active" : ""
          }`}
          onClick={abrirFileBrowser}
        >
          FileBrowser
        </button>
      </div>

      {vista === "explorar" && (
        <nav className="nas-crumbs" aria-label="Ruta">
          <button type="button" onClick={() => irAExplorar("")}>
            NAS
          </button>
          {partes.map((parte, indice) => {
            const destino = partes.slice(0, indice + 1).join("/");
            return (
              <span key={destino} className="nas-crumb">
                <span aria-hidden="true">/</span>
                <button type="button" onClick={() => irAExplorar(destino)}>
                  {parte}
                </button>
              </span>
            );
          })}
        </nav>
      )}

      {error && <p className="nas-error">{error}</p>}
      {cargando && <p className="nas-status">Cargando…</p>}

      {vista === "explorar" && listado && !cargando && (
        <>
          {listado.dirs.length > 0 && (
            <ul className="nas-grid">
              {listado.dirs.map((dir) => (
                <li key={dir.path}>
                  <button
                    type="button"
                    className="nas-folder"
                    onClick={() => irAExplorar(dir.path)}
                  >
                    <FolderIcon />
                    <span>{dir.name}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
          {listado.files.length > 0 && (
            <div className="nas-list">
              {listado.files.map((item) => (
                <Archivo key={item.path} item={item} onAbrir={abrir} {...acciones(item)} />
              ))}
            </div>
          )}
          {listadoVacio && (
            <p className="nas-empty">Esta carpeta está vacía.</p>
          )}
        </>
      )}

      {vista === "recientes" && !cargando && (
        <>
          {archivosVista.length === 0 ? (
            <p className="nas-empty">Aún no hay archivos en el NAS.</p>
          ) : (
            <div className="nas-list">
              {archivosVista.map((item) => (
                <Archivo key={item.path} item={item} onAbrir={abrir} {...acciones(item)} />
              ))}
            </div>
          )}
        </>
      )}

      {vista === "buscar" && resultados && !cargando && (
        <>
          <p className="nas-status">
            {resultados.total_matches === 0
              ? `Sin resultados para «${resultados.query}».`
              : `${resultados.total_matches} coincidencia${
                  resultados.total_matches === 1 ? "" : "s"
                } para «${resultados.query}».`}
            {!resultados.ready && " El índice todavía se está construyendo."}
          </p>
          {resultados.results.length > 0 && (
            <div className="nas-list">
              {resultados.results.map((item) => (
                <div key={item.path} className="nas-file-wrap">
                  <Archivo item={item} onAbrir={abrir} {...acciones(item)} />
                  {item.snippet && <p className="nas-snippet">{item.snippet}</p>}
                  {item.match === "contenido" && (
                    <span className="nas-badge">coincide en el contenido</span>
                  )}
                </div>
              ))}
            </div>
          )}
        </>
      )}

      {vista === "filebrowser" && (
        <div className="nas-frame-wrap">
          {frameListo && (
            <iframe
              className="files-frame"
              src="/files/"
              title="FileBrowser: archivos del NAS"
            />
          )}
        </div>
      )}

      <a
        className="portainer-link"
        href={portainer}
        target="_blank"
        rel="noopener noreferrer"
      >
        <ShieldIcon />
        <span>Portainer</span>
        <span className="muted">contenedores · otro puerto</span>
        <ExternalIcon className="portainer-go" />
      </a>

      {visor && (
        <div
          className="nas-lightbox"
          role="dialog"
          aria-modal="true"
          aria-label={visor.name}
          onClick={() => setVisor(null)}
        >
          <button
            type="button"
            className="icon-btn nas-lightbox-close"
            aria-label="Cerrar"
            onClick={() => setVisor(null)}
          >
            <XIcon />
          </button>
          <figure onClick={(event) => event.stopPropagation()}>
            <img src={api.nasFileUrl(visor.path)} alt={visor.name} />
            <figcaption>
              <span>{visor.name}</span>
              <a
                className="ghost-btn"
                href={api.nasFileUrl(visor.path, true)}
                download={visor.name}
              >
                Descargar
              </a>
            </figcaption>
          </figure>
        </div>
      )}
    </div>
  );
}
