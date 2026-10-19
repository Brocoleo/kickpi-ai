import { ExternalIcon, FolderIcon, ShieldIcon } from "./icons";

function targets() {
  const host = window.location.hostname;
  const local = host === "localhost" || host === "127.0.0.1";
  const base = local ? "kickpi-jarvis.tail1e3b4d.ts.net" : host;
  return {
    filebrowser: `https://${base}:8443/`,
    portainer: `https://${base}/`,
  };
}

export function Files() {
  const { filebrowser, portainer } = targets();

  return (
    <div className="screen">
      <div className="screen-head">
        <h1 className="screen-title">Archivos</h1>
      </div>

      <a className="card link-card" href={filebrowser} target="_blank" rel="noopener noreferrer">
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
          Se abren en otra pestaña y piden su propia contraseña. Jarvis no
          almacena esas credenciales.
        </p>
        <p className="muted">
          Cuando conectes el disco USB, los archivos del NAS aparecerán en
          <code>/mnt/data</code>.
        </p>
      </div>
    </div>
  );
}
