import { ExternalIcon, FolderIcon, ShieldIcon } from "./icons";

/**
 * FileBrowser se sirve a través del agente (/files/*), así que comparte
 * origen con la PWA: mismo TLS, misma sesión de Jarvis y sin puerto extra.
 *
 * Portainer NO admite subrutas (su frontend usa rutas absolutas /api/*,
 * /static/*), así que sigue en su propio puerto de Tailscale Serve.
 */
const PORTAINER_PORT = 9443;

function portainerUrl() {
  const host = window.location.hostname;
  const base = host === "localhost" || host === "127.0.0.1"
    ? "kickpi-jarvis.tail1e3b4d.ts.net"
    : host;
  return `https://${base}:${PORTAINER_PORT}/`;
}

export function Files() {
  const portainer = portainerUrl();

  return (
    <div className="screen">
      <div className="screen-head">
        <h1 className="screen-title">Archivos</h1>
      </div>

      <a className="card link-card" href="/files/" target="_blank" rel="noopener noreferrer">
        <span className="link-icon">
          <FolderIcon />
        </span>
        <span className="link-body">
          <span className="link-title">FileBrowser</span>
          <span className="link-sub">Explorar y subir archivos al NAS</span>
        </span>
        <ExternalIcon className="link-go" />
      </a>

      <a className="card link-card" href={portainer} target="_blank" rel="noopener noreferrer">
        <span className="link-icon">
          <ShieldIcon />
        </span>
        <span className="link-body">
          <span className="link-title">Portainer</span>
          <span className="link-sub">Contenedores, imágenes y volúmenes</span>
        </span>
        <ExternalIcon className="link-go" />
      </a>

      <div className="card note">
        <p>
          FileBrowser abre dentro de Jarvis (mismo origen, sin otro puerto).
          La primera vez pide su propia contraseña, que Jarvis no almacena.
        </p>
        <p className="muted">
          Portainer vive en su propio puerto porque no admite subrutas. Se abre
          en otra pestaña y pide su propia contraseña.
        </p>
      </div>
    </div>
  );
}
