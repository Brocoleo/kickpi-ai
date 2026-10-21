import { useEffect } from "react";
import { ExternalIcon, FolderIcon, ShieldIcon } from "./icons";

/** Puertos dedicados (TLS de Tailscale Serve en cada uno). */
const FILEBROWSER_PORT = 8443;
const PORTAINER_PORT = 9443;

function targets() {
  const host = window.location.hostname;
  const local = host === "localhost" || host === "127.0.0.1";
  const base = local ? "kickpi-jarvis.tail1e3b4d.ts.net" : host;
  return {
    filebrowser: `https://${base}:${FILEBROWSER_PORT}/`,
    portainer: `https://${base}:${PORTAINER_PORT}/`,
    filebrowserOrigin: `https://${base}:${FILEBROWSER_PORT}`,
  };
}

export function Files() {
  const { filebrowser, portainer, filebrowserOrigin } = targets();

  // Preconnect: al abrir la pestaña Archivos ya se negocia TLS/DNS con
  // FileBrowser, de modo que el salto se sienta inmediato.
  useEffect(() => {
    const link = document.createElement("link");
    link.rel = "preconnect";
    link.href = filebrowserOrigin;
    link.crossOrigin = "";
    document.head.appendChild(link);
    return () => {
      link.remove();
    };
  }, [filebrowserOrigin]);

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
