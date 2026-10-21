import { useCallback, useRef } from "react";
import { ExternalIcon, RefreshIcon, ShieldIcon } from "./icons";

/**
 * FileBrowser se sirve a través del agente (/files/*), así que comparte
 * origen con la PWA: mismo TLS, misma sesión de Jarvis y sin puerto extra.
 * Eso permite incrustarlo en un iframe sin pelear con cookies de terceros.
 *
 * El iframe se monta UNA vez (App lo mantiene oculto al cambiar de pestaña):
 * cambiar de pestaña y volver no recarga FileBrowser ni pierde la carpeta en
 * la que estabas. El contenedor de FileBrowser queda fuera del alcance de
 * esta pantalla: aquí solo se enmarca y se da una salida por si algo falla.
 *
 * Portainer NO admite subrutas (su frontend usa rutas absolutas /api/*,
 * /static/*), así que sigue en su propio puerto de Tailscale Serve.
 */
const PORTAINER_PORT = 9443;

function portainerUrl() {
  const host = window.location.hostname;
  const base =
    host === "localhost" || host === "127.0.0.1"
      ? "kickpi-jarvis.tail1e3b4d.ts.net"
      : host;
  return `https://${base}:${PORTAINER_PORT}/`;
}

export function Files() {
  const frame = useRef<HTMLIFrameElement>(null);
  const portainer = portainerUrl();

  const recargar = useCallback(() => {
    // Mismo origen, así que se puede recargar sin remontar el iframe (que
    // volvería a pedir el HTML y perdería el estado de navegación).
    frame.current?.contentWindow?.location.reload();
  }, []);

  return (
    <div className="screen files-screen">
      <div className="screen-head">
        <h1 className="screen-title">Archivos</h1>
        <div className="head-meta">
          <button
            type="button"
            className="icon-btn"
            onClick={recargar}
            aria-label="Recargar FileBrowser"
          >
            <RefreshIcon />
          </button>
          <a
            className="icon-btn"
            href="/files/"
            target="_blank"
            rel="noopener noreferrer"
            aria-label="Abrir FileBrowser en otra pestaña"
          >
            <ExternalIcon />
          </a>
        </div>
      </div>

      {/* Sin sandbox: subir y descargar archivos necesita privilegios
          plenos. El iframe es del mismo origen, así que no hay frontera de
          confianza que relajar. */}
      <iframe
        ref={frame}
        className="files-frame"
        src="/files/"
        title="FileBrowser: archivos del NAS"
      />

      <a className="portainer-link" href={portainer} target="_blank" rel="noopener noreferrer">
        <ShieldIcon />
        <span>Portainer</span>
        <span className="muted">contenedores · otro puerto</span>
        <ExternalIcon className="portainer-go" />
      </a>
    </div>
  );
}
