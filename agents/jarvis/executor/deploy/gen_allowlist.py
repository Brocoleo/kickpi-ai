#!/usr/bin/env python3
"""Genera el allow-list del forced command SSH a partir del registro de hosts.

El allow-list se instala en el EQUIPO DESTINO. Se deriva de hosts.yml para
que el filtro remoto y el registro del ejecutor no diverjan.

Incluye el PATH donde se buscan los ejecutables: el de la sesion SSH forzada
no incluye nvm, donde vive node en este equipo.

    python3 gen_allowlist.py executor/config/hosts.yml macbook
"""
from __future__ import annotations

import json
import os
import sys

import yaml

# 'npm' y 'make' quedan fuera: pueden ejecutar codigo arbitrario y no hacen
# falta para el catalogo actual.
BINARIES = [
    "uname", "uptime", "df", "free", "ls", "git", "node",
    "readlink", "wc", "cat", "head", "tail", "grep", "find",
]

# Forma de invocacion admitida por binario (ver wrapper).
RULES = {
    "node": {"argv1": "--test"},
    "git": {
        "subcommands": [
            "status", "log", "rev-parse", "diff", "ls-files",
            "branch", "show", "describe", "rev-list",
        ]
    },
}

BASE_PATH = ["/usr/local/bin", "/usr/bin", "/bin"]

# Home del usuario: se busca la version de node instalada con nvm.
HOME = os.path.expanduser("~")
NVM_VERSIONS = os.path.join(HOME, ".nvm", "versions", "node")


def node_path() -> list[str]:
    """Ruta del bin de node si existe (nvm)."""
    extra: list[str] = []
    if os.path.isdir(NVM_VERSIONS):
        for version in sorted(os.listdir(NVM_VERSIONS)):
            candidate = os.path.join(NVM_VERSIONS, version, "bin")
            if os.path.isdir(candidate):
                extra.append(candidate)
    return extra


def main() -> None:
    if len(sys.argv) < 3:
        sys.exit("uso: gen_allowlist.py <hosts.yml> <host_id> [salida]")

    with open(sys.argv[1], encoding="utf-8") as fh:
        registry = yaml.safe_load(fh) or {}

    host_id = sys.argv[2]
    out_path = sys.argv[3] if len(sys.argv) > 3 else None

    host = next((h for h in (registry.get("hosts") or []) if h.get("id") == host_id), None)
    if host is None:
        sys.exit(f"host no encontrado en el registro: {host_id}")

    roots = [str(r) for r in (host.get("roots") or [])]
    if not roots:
        sys.exit(f"el host {host_id} no tiene raices autorizadas")

    allowlist = {
        "host": host_id,
        "roots": roots,
        "binaries": BINARIES,
        "rules": RULES,
        "path": BASE_PATH + node_path(),
    }

    text = json.dumps(allowlist, indent=2, ensure_ascii=False) + "\n"
    if out_path:
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"allow-list escrito en {out_path}")
        print(f"  raices    : {len(roots)}")
        print(f"  path      : {', '.join(allowlist['path'])}")
    else:
        print(text, end="")


if __name__ == "__main__":
    main()
