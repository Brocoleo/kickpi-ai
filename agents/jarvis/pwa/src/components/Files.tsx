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
            {[item.folder, formatoTamano(item.size), fecha(item.modified)]
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
  // Rutas borradas en esta sesión: se ocultan aunque el índice tarde un
  // instante en reflejarlo (y evita que un refresco lento las resucite).
  const [ocultos, setOcultos] = useState<string[]>([]);
  const [inbox, setInbox] = useState("files/Entrada");
  const [frameListo, setFrameListo] = useState(false);
  const portainer = portainerUrl();

  const gestionarError = useCallback(
    (err: unknown) => {
      if (err instanceof ApiError && err.status === 401) onUnauthorized();
      setError(mensajeError(err));
    },
    [onUnauthorized],
  );

  type OpcionesCarga = { silencioso?: boolean };

  const cargarRecientes = useCallback(async (opciones?: OpcionesCarga) => {
    if (!opciones?.silencioso) setCargando(true);
    setError(null);
    try {
      const data = await api.nasRecent(30);
      setRecientes(Array.isArray(data.files) ? data.files : []);
      if (data.error) setError(data.error);
    } catch (err) {
      gestionarError(err);
    } finally {
      if (!opciones?.silencioso) setCargando(false);
    }
  }, [gestionarError]);

  const cargarCarpeta = useCallback(async (nuevaRuta: string, opciones?: OpcionesCarga) => {
    if (!opciones?.silencioso) setCargando(true);
    setError(null);
    try {
      const data = await api.nasList(nuevaRuta);
      setListado(data);
      setRuta(data.path || nuevaRuta);
      if (data.error) setError(data.error);
    } catch (err) {
      gestionarError(err);
    } finally {
      if (!opciones?.silencioso) setCargando(false);
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
    // La ruta del inbox la declara el backend (/api/nas/status): la PWA no
    // la adivina. Si falla, el fallback solo afecta al atajo de navegación.
    api
      .nasStatus()
      .then((datos) => {
        if (datos.inbox) setInbox(datos.inbox);
      })
      .catch(() => {
        /* sin drama: la navegación sigue funcionando con el fallback */
      });
  }, []);

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
      setConfirmarBorrado(null);
      // Ocultar YA. El DELETE es rápido; lo lento era recargar el índice
      // (hasta 15 min de caché), y el usuario se quedaba mirando el archivo.
      setOcultos((prev) =>
        prev.includes(item.path) ? prev : [...prev, item.path],
      );
      setVisor((actual) =>
        actual && actual.path === item.path ? null : actual,
      );
      try {
        await api.deleteNasFile(item.path, csrfToken);
        // El backend ya invalidó su índice: recargar sin bloquear la UI
        // (silencioso: no queremos que toda la lista parpadee a «Cargando…»).
        void cargarRecientes({ silencioso: true });
        if (vista === "explorar") {
          void cargarCarpeta(ruta, { silencioso: true });
        }
        if (vista === "buscar" && resultados) {
          void api
            .nasSearch(resultados.query)
            .then(setResultados)
            .catch(gestionarError);
        }
      } catch (err) {
        // Si falló, vuelve a mostrarlo: no mentimos con un borrado falso.
        setOcultos((prev) => prev.filter((p) => p !== item.path));
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
  const visible = (item: NasEntry) => !ocultos.includes(item.path);
  const archivosExplorar = (listado?.files ?? []).filter(visible);
  const recientesVisibles = recientes.filter(visible);
  const resultadosVisibles = (resultados?.results ?? []).filter(visible);
  const archivosVista =
    vista === "buscar" ? resultadosVisibles : recientesVisibles;
  const totalCoincidencias = resultados
    ? Math.max(
        0,
        resultados.total_matches -
          (resultados.results.length - resultadosVisibles.length),
      )
    : 0;
  const listadoVacio =
    vista === "explorar" &&
    !!listado &&
    !listado.dirs.length &&
    !archivosExplorar.length;

  // Borrar solo se ofrece para adjuntos propios: lo decide el backend con
  // `deletable` (él es quien conoce la ruta real de Entrada/). La UI no
  // adivina prefijos: así no puede volver a desincronizarse.
  const acciones = (item: NasEntry) =>
    item.deletable
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
          {archivosExplorar.length > 0 && (
            <div className="nas-list">
              {archivosExplorar.map((item) => (
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
          <button
            type="button"
            className="ghost-btn nas-inbox-link"
            onClick={() => irAExplorar(inbox)}
          >
            <FolderIcon />
            Ver carpetas de Entrada
          </button>
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
            {totalCoincidencias === 0
              ? `Sin resultados para «${resultados.query}».`
              : `${totalCoincidencias} coincidencia${
                  totalCoincidencias === 1 ? "" : "s"
                } para «${resultados.query}».`}
            {!resultados.ready && " El índice todavía se está construyendo."}
          </p>
          {resultadosVisibles.length > 0 && (
            <div className="nas-list">
              {resultadosVisibles.map((item) => (
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
              <div className="nas-lightbox-actions">
                {visor.deletable &&
                  (confirmarBorrado === visor.path ? (
                    <>
                      <button
                        type="button"
                        className="ghost-btn nas-danger"
                        onClick={() => void borrar(visor)}
                      >
                        Borrar
                      </button>
                      <button
                        type="button"
                        className="ghost-btn"
                        onClick={() => setConfirmarBorrado(null)}
                      >
                        No
                      </button>
                    </>
                  ) : (
                    <button
                      type="button"
                      className="icon-btn nas-lightbox-delete"
                      aria-label={`Borrar ${visor.name}`}
                      title="Borrar de Entrada"
                      onClick={() => setConfirmarBorrado(visor.path)}
                    >
                      <TrashIcon />
                    </button>
                  ))}
              </div>
            </figcaption>
          </figure>
        </div>
      )}
    </div>
  );
}
