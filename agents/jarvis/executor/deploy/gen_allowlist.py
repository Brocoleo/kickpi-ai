#!/usr/bin/env python3
"""Genera el allow-list del forced command SSH a partir del registro de hosts.

El allow-list se instala en el EQUIPO DESTINO. Se deriva de hosts.yml para
que el filtro remoto y el registro del ejecutor no diverjan.

    python3 gen_allowlist.py executor/config/hosts.yml macbook
"""
from __future__ import annotations

import json
import sys

import yaml

BINARIES = [
    "uname", "uptime", "df", "free", "ls", "git", "node", "npm",
    "make", "readlink", "wc", "cat", "head", "tail", "grep", "find",
]


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
    }

    text = json.dumps(allowlist, indent=2, ensure_ascii=False) + "\n"
    if out_path:
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"allow-list escrito en {out_path} ({len(roots)} raices)")
    else:
        print(text, end="")


if __name__ == "__main__":
    main()
