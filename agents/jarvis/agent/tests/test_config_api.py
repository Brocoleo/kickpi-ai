"""Configuración del NAS en caliente: sesión humana, validación y efecto real.

Lo que se protege:
- el token de API no cambia ajustes (solo una sesión de navegador + CSRF),
- los límites y el JSON de reglas se validan con mensajes claros,
- lo guardado se aplica en la siguiente subida, sin reiniciar.
"""
from __future__ import annotations

import json

from conftest import API_TOKEN


def test_config_exige_sesion_de_navegador(client) -> None:
    assert client.get("/api/admin/nas").status_code == 401
    assert client.patch("/api/admin/nas", json={"upload_max_mb": 10}).status_code == 401
    # El token de API autentica, pero no basta para una acción humana.
    assert (
        client.get(
            "/api/admin/nas", headers={"X-API-Token": API_TOKEN}
        ).status_code
        == 403
    )


def test_config_inicial_sale_del_entorno(browser) -> None:
    datos = browser.get("/api/admin/nas").json()
    assert datos["inbox"] == "files/Entrada"
    assert datos["upload_max_mb"] == 50
    assert datos["upload_rate_per_min"] == 60
    assert datos["classifier_rules"] is None


def test_patch_guarda_y_persiste(browser) -> None:
    respuesta = browser.patch(
        "/api/admin/nas",
        json={"upload_max_mb": 12, "upload_rate_per_min": 5},
    )
    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json()["upload_max_mb"] == 12
    # Se relee de la BD, no de la respuesta.
    assert browser.get("/api/admin/nas").json()["upload_rate_per_min"] == 5


def test_patch_rechaza_valores_fuera_de_rango(browser) -> None:
    respuesta = browser.patch("/api/admin/nas", json={"upload_max_mb": 0})
    assert respuesta.status_code == 400
    assert "entre 1 y 500" in respuesta.json()["detail"]

    respuesta = browser.patch("/api/admin/nas", json={"upload_rate_per_min": 601})
    assert respuesta.status_code == 400
    assert "entre 0 y 600" in respuesta.json()["detail"]

    respuesta = browser.patch("/api/admin/nas", json={"upload_max_mb": "muchos"})
    assert respuesta.status_code == 400


def test_patch_valida_el_json_de_reglas(browser) -> None:
    respuesta = browser.patch(
        "/api/admin/nas", json={"classifier_rules": "{no es json"}
    )
    assert respuesta.status_code == 400
    assert "JSON inválido" in respuesta.json()["detail"]

    respuesta = browser.patch(
        "/api/admin/nas", json={"classifier_rules": '{"rules": []}'}
    )
    assert respuesta.status_code == 400
    assert "Reglas" in respuesta.json()["detail"]


def test_patch_no_permite_claves_desconocidas(browser) -> None:
    # La ruta del inbox está atada al volumen: no se cambia desde la PWA.
    respuesta = browser.patch("/api/admin/nas", json={"inbox": "otra"})
    assert respuesta.status_code == 422


def test_el_limite_de_subida_se_aplica_al_momento(browser) -> None:
    assert (
        browser.patch(
            "/api/admin/nas", json={"upload_max_mb": 1}
        ).status_code
        == 200
    )
    respuesta = browser.post(
        "/api/files/upload",
        files={
            "file": (
                "grande.bin",
                b"x" * (1024 * 1024 + 1),
                "application/octet-stream",
            )
        },
    )
    assert respuesta.status_code == 413
    assert "1 MB" in respuesta.json()["detail"]


def test_reglas_personalizadas_clasifican_la_subida(browser) -> None:
    reglas = json.dumps(
        {
            "default_folder": "Varios",
            "rules": [{"folder": "Raros", "extensions": [".xyz"]}],
        }
    )
    assert (
        browser.patch(
            "/api/admin/nas", json={"classifier_rules": reglas}
        ).status_code
        == 200
    )
    subido = browser.post(
        "/api/files/upload",
        files={"file": ("dato.xyz", b"1", "application/octet-stream")},
    ).json()
    assert subido["path"].startswith("files/Entrada/Raros/"), subido

    # Cadena vacía = volver a las reglas de fábrica.
    assert (
        browser.patch(
            "/api/admin/nas", json={"classifier_rules": ""}
        ).status_code
        == 200
    )
    otro = browser.post(
        "/api/files/upload",
        files={"file": ("dato2.xyz", b"1", "application/octet-stream")},
    ).json()
    assert otro["path"].startswith("files/Entrada/Documentos/"), otro
