"""Pruebas de C3: `host_git_diff_stat`, la operacion con aprobacion humana.

La operacion es INOCUA (solo lectura: `git --no-optional-locks -C <ruta>
diff --stat` no escribe, no dispara hooks y no toca el indice) pero NUNCA es
auto-aprobable. Sirve para validar el flujo completo: el LLM pide -> el
ejecutor abre una solicitud pendiente -> un humano autenticado aprueba ->
se ejecuta exactamente lo persistido.

Se comprueba tambien que NO se puede by-passear:
- operacion inventada (bypass del catalogo),
- rutas fuera de raiz, con '..', relativas o con symlink que escapa,
- modificacion de los argumentos guardados,
- replay y doble ejecucion (un solo uso),
- y que el LLM no puede aprobar, rechazar ni ejecutar por su cuenta.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import yaml

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.main import app  # noqa: E402

EXEC_HEADER = {"X-Executor-Token": "token-ejecutor"}
APPROVALS_HEADER = {"X-Approvals-Token": "token-aprobaciones"}

#: Catalogo REAL que se despliega (no una copia de pruebas).
CATALOG_REAL = Path(__file__).resolve().parents[1] / "config" / "catalog.yml"
#: Cliente del agente: su mapa cerrado debe conocer todas las operaciones.
AGENT_CLIENT = Path(__file__).resolve().parents[2] / "agent" / "app" / "executor_client.py"

OP = "host_git_diff_stat"
ARGV_ESPERADO = ["git", "--no-optional-locks", "-C", "{path}", "diff", "--stat"]


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Repo git REAL con un cambio sin commitear (para la prueba de extremo
    a extremo). Se salta si no hay git en el entorno."""
    if shutil.which("git") is None:
        pytest.skip("git no disponible en este entorno")

    path = tmp_path / "repo"
    path.mkdir()

    def git(*args: str) -> None:
        subprocess.run(
            ["git", *args], cwd=path, check=True, capture_output=True, text=True
        )

    git("init", "-q")
    (path / "archivo.txt").write_text("uno\n", encoding="utf-8")
    git("add", "archivo.txt")
    git("-c", "user.email=prueba@jarvis.local", "-c", "user.name=prueba",
        "commit", "-q", "-m", "inicial")
    # Cambio sin commitear: es lo que `git diff --stat` debe resumir.
    (path / "archivo.txt").write_text("uno\ndos\ntres\n", encoding="utf-8")
    return path


@pytest.fixture
def api(tmp_path, monkeypatch, repo):
    """Ejecutor con el catalogo REAL y un host local cuya raiz es el repo."""
    hosts_yml = tmp_path / "hosts.yml"
    hosts_yml.write_text(
        "hosts:\n"
        "  - id: localtest\n"
        "    label: Local de pruebas\n"
        "    transport: local\n"
        "    enabled: true\n"
        f'    roots: ["{repo}"]\n'
        "projects: []\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("JARVIS_EXECUTOR_AUDIT_DB", str(tmp_path / "audit.db"))
    monkeypatch.setenv("JARVIS_EXECUTOR_TOKEN", "token-ejecutor")
    monkeypatch.setenv("JARVIS_EXECUTOR_APPROVALS_TOKEN", "token-aprobaciones")
    monkeypatch.setenv("JARVIS_EXECUTOR_HOSTS_FILE", str(hosts_yml))
    monkeypatch.setenv("JARVIS_EXECUTOR_CATALOG_FILE", str(CATALOG_REAL))
    get_settings.cache_clear()
    with TestClient(app) as client:
        yield client
    get_settings.cache_clear()


def _pedir(api, path: str) -> dict:
    """El LLM pide la operacion: devuelve la solicitud pendiente."""
    resp = api.post(
        "/exec",
        headers=EXEC_HEADER,
        json={"host": "localtest", "operation": OP, "params": {"path": path}},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _aprobar(api, approval_id: str, who: str = "leandro") -> dict:
    resp = api.post(
        f"/approvals/{approval_id}/approve",
        headers=APPROVALS_HEADER,
        json={"approved_by": who},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["approval"]


# --------------------------------------------------------------------------- #
# El catalogo desplegado
# --------------------------------------------------------------------------- #
def test_el_catalogo_real_declara_la_operacion_como_no_auto():
    data = yaml.safe_load(CATALOG_REAL.read_text(encoding="utf-8"))
    ops = {op["id"]: op for op in data["operations"]}
    assert OP in ops, "la operacion de demostracion no esta en el catalogo"
    op = ops[OP]
    assert op["auto"] is False, "esta operacion NUNCA debe ser auto-aprobable"
    assert op["risk"] == "read"
    assert op["path_param"] == "path"
    assert op["command"] == ARGV_ESPERADO
    assert op["timeout"] == 30


def test_el_catalogo_no_ofrece_una_via_generica():
    """No hay ningun comando libre: todas las operaciones son argv fijos."""
    data = yaml.safe_load(CATALOG_REAL.read_text(encoding="utf-8"))
    permitidos = {"uname", "uptime", "df", "free", "ls", "git", "node"}
    for op in data["operations"]:
        command = op.get("command")
        if command is None:
            # Operacion basada en proyecto: el argv lo define el proyecto
            # autorizado, no una cadena libre.
            assert op.get("project_param")
            continue
        assert isinstance(command, list) and command
        assert os.path.basename(command[0]) in permitidos
        for part in command:
            assert not any(bad in part for bad in (";", "&&", "|", "$(", "`"))


def test_operations_expone_la_operacion_con_requiere_aprobacion(api):
    resp = api.get("/operations", headers=EXEC_HEADER)
    assert resp.status_code == 200
    ops = {item["id"]: item for item in resp.json()["operations"]}
    assert OP in ops
    assert ops[OP]["requires_approval"] is True
    assert ops[OP]["auto"] is False
    assert ops[OP]["params"] == ["path"]
    # Las auto-aprobables siguen siendolo.
    assert ops["host_uname"]["requires_approval"] is False


def test_el_mapa_cerrado_del_agente_conoce_todas_las_operaciones():
    """Sin desincronizar la vista del LLM con el catalogo desplegado."""
    if not AGENT_CLIENT.is_file():
        pytest.skip("el cliente del agente no esta junto a este arbol")
    data = yaml.safe_load(CATALOG_REAL.read_text(encoding="utf-8"))
    texto = AGENT_CLIENT.read_text(encoding="utf-8")
    for op in data["operations"]:
        assert f'"{op["id"]}"' in texto, f"el agente no conoce {op['id']}"


# --------------------------------------------------------------------------- #
# Politica: la operacion se valida pero NO se ejecuta
# --------------------------------------------------------------------------- #
def test_la_operacion_exige_aprobacion_y_no_se_ejecuta(api, repo, monkeypatch):
    calls = []

    async def fake_run(host, argv, timeout=None):
        calls.append(argv)
        from app.runner import RunResult

        return RunResult(0, "EJECUTADO", "", 1)

    monkeypatch.setattr("app.main.run", fake_run)

    data = _pedir(api, str(repo))
    assert data["state"] == "pending_approval"
    assert data["denied"] is False

    approval = data["approval"]
    assert approval["status"] == "pending"
    assert approval["operation"] == OP
    assert approval["requested_by"] == "agente"
    assert approval["approved_by"] is None
    assert approval["risk"] == "read"
    # El argv persistido es el NORMALIZADO (ruta ya resuelta en el host).
    assert approval["argv"] == [
        "git", "--no-optional-locks", "-C", os.path.realpath(repo), "diff", "--stat",
    ]
    assert calls == [], "la operacion NO debe ejecutarse sin humano"


def test_ruta_fuera_de_raices_es_deniego_normal(api, tmp_path):
    """Nada que aprobar: no hay solicitud y no hay puerta trasera."""
    fuera = tmp_path / "fuera"
    fuera.mkdir()
    resp = api.post(
        "/exec",
        headers=EXEC_HEADER,
        json={"host": "localtest", "operation": OP, "params": {"path": str(fuera)}},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["denied"] is True
    assert "state" not in data, "un deniego no es una solicitud"
    assert "raices" in data["reason"]
    assert api.get("/approvals", headers=APPROVALS_HEADER).json()["count"] == 0


def test_symlink_que_escapa_se_deniega(api, repo, tmp_path):
    fuera = tmp_path / "fuera"
    fuera.mkdir()
    (fuera / "secreto.txt").write_text("no_should_read", encoding="utf-8")
    enlace = repo / "enlace-fuera"
    enlace.symlink_to(fuera)

    resp = api.post(
        "/exec",
        headers=EXEC_HEADER,
        json={"host": "localtest", "operation": OP, "params": {"path": str(enlace)}},
    )
    data = resp.json()
    assert data["denied"] is True
    assert "raices" in data["reason"]
    assert api.get("/approvals", headers=APPROVALS_HEADER).json()["count"] == 0


def test_ruta_con_puntos_dos_o_relativa_se_deniega(api, repo):
    for ruta in (f"{repo}/../fuera", "repo/relativa", "../etc"):
        resp = api.post(
            "/exec",
            headers=EXEC_HEADER,
            json={"host": "localtest", "operation": OP, "params": {"path": ruta}},
        )
        data = resp.json()
        assert data["denied"] is True, ruta
        assert "state" not in data, ruta


def test_operacion_inventada_no_crea_solicitud(api):
    """Bypass del catalogo: lo que no esta, no existe."""
    resp = api.post(
        "/exec",
        headers=EXEC_HEADER,
        json={"host": "localtest", "operation": "host_git_pull", "params": {"path": "/tmp"}},
    )
    data = resp.json()
    assert data["denied"] is True
    assert "no permitida" in data["reason"]
    assert api.get("/approvals", headers=APPROVALS_HEADER).json()["count"] == 0


def test_host_deshabilitado_no_crea_solicitud(api, tmp_path, monkeypatch):
    hosts_yml = tmp_path / "hosts-off.yml"
    hosts_yml.write_text(
        "hosts:\n"
        "  - id: localtest\n"
        "    label: Local\n"
        "    transport: local\n"
        "    enabled: false\n"
        f'    roots: ["{tmp_path}"]\n'
        "projects: []\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("JARVIS_EXECUTOR_HOSTS_FILE", str(hosts_yml))
    resp = api.post(
        "/exec",
        headers=EXEC_HEADER,
        json={"host": "localtest", "operation": OP, "params": {"path": str(tmp_path)}},
    )
    assert resp.json()["denied"] is True
    assert "habilitado" in resp.json()["reason"]
    assert api.get("/approvals", headers=APPROVALS_HEADER).json()["count"] == 0


# --------------------------------------------------------------------------- #
# El LLM no puede decidir
# --------------------------------------------------------------------------- #
def test_el_llm_no_puede_aprobar_rechazar_ni_ejecutar(api, repo):
    approval = _pedir(api, str(repo))["approval"]
    aid = approval["id"]

    # Con el token del ejecutor (el del LLM) las rutas de aprobacion estan
    # cerradas: 401 en todas.
    assert api.get("/approvals", headers=EXEC_HEADER).status_code == 401
    assert api.post(
        f"/approvals/{aid}/approve", headers=EXEC_HEADER, json={"approved_by": "llm"}
    ).status_code == 401
    assert api.post(
        f"/approvals/{aid}/reject", headers=EXEC_HEADER, json={"approved_by": "llm"}
    ).status_code == 401
    assert api.post(f"/approvals/{aid}/execute", headers=EXEC_HEADER).status_code == 401

    # Sin token tampoco.
    assert api.post(
        f"/approvals/{aid}/approve", json={"approved_by": "llm"}
    ).status_code == 401

    # Y no puede colar el approval_id por /exec.
    resp = api.post(
        "/exec",
        headers=EXEC_HEADER,
        json={
            "host": "localtest",
            "operation": OP,
            "params": {"path": str(repo)},
            "approval_id": aid,
        },
    )
    assert resp.status_code == 422
    # La solicitud sigue pendiente: nadie la ha tocado.
    assert api.get(f"/approvals/{aid}", headers=APPROVALS_HEADER).json()["approval"][
        "status"
    ] == "pending"


def test_aprobar_sin_identidad_humana_se_rechaza(api, repo):
    approval = _pedir(api, str(repo))["approval"]
    resp = api.post(
        f"/approvals/{approval['id']}/approve",
        headers=APPROVALS_HEADER,
        json={"approved_by": ""},
    )
    assert resp.status_code == 400
    assert api.get(f"/approvals/{approval['id']}", headers=APPROVALS_HEADER).json()[
        "approval"
    ]["status"] == "pending"


def test_ejecutar_una_solicitud_pendiente_se_rechaza(api, repo):
    approval = _pedir(api, str(repo))["approval"]
    resp = api.post(f"/approvals/{approval['id']}/execute", headers=APPROVALS_HEADER)
    assert resp.status_code == 409
    assert "pending" in resp.json()["detail"]


# --------------------------------------------------------------------------- #
# Flujo completo con git REAL
# --------------------------------------------------------------------------- #
def test_flujo_completo_aprobar_y_ejecutar_git_real(api, repo):
    """El flujo real: pedir -> aprobar (humano) -> ejecutar (git de verdad)."""
    approval = _pedir(api, str(repo))["approval"]
    aid = approval["id"]

    approved = _aprobar(api, aid)
    assert approved["status"] == "approved"
    assert approved["approved_by"] == "leandro"
    assert approved["requested_by"] == "agente"

    resp = api.post(f"/approvals/{aid}/execute", headers=APPROVALS_HEADER)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["state"] == "consumed"
    assert body["exit_code"] == 0
    assert body["approval_id"] == aid
    assert "archivo.txt" in body["stdout"]
    assert "1 file changed" in body["stdout"]

    # Un solo uso: el segundo intento no ejecuta nada.
    resp2 = api.post(f"/approvals/{aid}/execute", headers=APPROVALS_HEADER)
    assert resp2.status_code == 409

    final = api.get(f"/approvals/{aid}", headers=APPROVALS_HEADER).json()["approval"]
    assert final["status"] == "consumed"
    assert final["approved_by"] == "leandro"
    assert final["exit_code"] == 0

    # Auditoria: una sola ejecucion permitida, con el approval_id en los params.
    conn = sqlite3.connect(get_settings().audit_db)
    rows = conn.execute(
        "SELECT operation, allowed, ok, exit_code, params_json FROM executions"
    ).fetchall()
    conn.close()
    audited = [r for r in rows if r[0] == OP and r[1] == 1]
    assert len(audited) == 1
    assert audited[0][3] == 0
    assert aid in audited[0][4]


def test_rechazar_impide_la_ejecucion(api, repo):
    approval = _pedir(api, str(repo))["approval"]
    resp = api.post(
        f"/approvals/{approval['id']}/reject",
        headers=APPROVALS_HEADER,
        json={"approved_by": "leandro", "reason": "ahora no"},
    )
    assert resp.status_code == 200
    resp2 = api.post(f"/approvals/{approval['id']}/execute", headers=APPROVALS_HEADER)
    assert resp2.status_code == 409
    assert "rejected" in resp2.json()["detail"]


def test_doble_ejecucion_concurrente_solo_ejecuta_una(api, repo):
    approval = _pedir(api, str(repo))["approval"]
    aid = approval["id"]
    _aprobar(api, aid)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(api.post, f"/approvals/{aid}/execute", headers=APPROVALS_HEADER)
            for _ in range(2)
        ]
        responses = [f.result() for f in futures]

    codes = sorted(r.status_code for r in responses)
    assert codes == [200, 409], codes
    states = {r.json().get("state") for r in responses if r.status_code == 200}
    assert states == {"consumed"}

    conn = sqlite3.connect(get_settings().audit_db)
    rows = conn.execute(
        "SELECT COUNT(*) FROM executions WHERE operation=? AND allowed=1", (OP,)
    ).fetchone()
    conn.close()
    assert rows[0] == 1, "solo puede quedar una ejecucion auditada"


def test_argumentos_modificados_invalidan_la_aprobacion(api, repo):
    """Si alguien altera el argv guardado, la ejecucion se invalida."""
    approval = _pedir(api, str(repo))["approval"]
    aid = approval["id"]
    _aprobar(api, aid)

    conn = sqlite3.connect(get_settings().audit_db)
    conn.execute(
        "UPDATE approvals SET argv_json=? WHERE id=?",
        (json.dumps(["git", "--no-optional-locks", "-C", "/etc", "diff", "--stat"]), aid),
    )
    conn.commit()
    conn.close()

    resp = api.post(f"/approvals/{aid}/execute", headers=APPROVALS_HEADER)
    assert resp.status_code == 409
    assert "alterada" in resp.json()["detail"]
    final = api.get(f"/approvals/{aid}", headers=APPROVALS_HEADER).json()["approval"]
    assert final["status"] == "failed"


def test_parametros_modificados_invalidan_la_aprobacion(api, repo):
    """Si cambian los params guardados, la politica ya no lo permite."""
    approval = _pedir(api, str(repo))["approval"]
    aid = approval["id"]
    _aprobar(api, aid)

    conn = sqlite3.connect(get_settings().audit_db)
    conn.execute(
        "UPDATE approvals SET params_json=? WHERE id=?",
        (json.dumps({"path": "/etc"}), aid),
    )
    conn.commit()
    conn.close()

    resp = api.post(f"/approvals/{aid}/execute", headers=APPROVALS_HEADER)
    assert resp.status_code == 409
    final = api.get(f"/approvals/{aid}", headers=APPROVALS_HEADER).json()["approval"]
    assert final["status"] == "failed"


def test_el_repo_no_se_modifica_por_la_operacion(api, repo):
    """La operacion es de solo lectura: el repo queda igual (salvo el diff)."""
    before = (repo / "archivo.txt").read_text(encoding="utf-8")
    status_before = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True
    ).stdout

    approval = _pedir(api, str(repo))["approval"]
    _aprobar(api, approval["id"])
    resp = api.post(f"/approvals/{approval['id']}/execute", headers=APPROVALS_HEADER)
    assert resp.status_code == 200

    assert (repo / "archivo.txt").read_text(encoding="utf-8") == before
    status_after = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True
    ).stdout
    assert status_after == status_before
    # No se ha creado ningun lock ni fichero nuevo en el repositorio.
    assert not (repo / ".git" / "index.lock").exists()
