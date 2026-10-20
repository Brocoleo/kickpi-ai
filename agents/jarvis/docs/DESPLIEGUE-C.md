# Despliegue de la Fase C (C1–C5) — procedimiento verificado

> **Nada de este documento se ha ejecutado.** Cada paso requiere la
> autorización explícita del administrador. Los comandos están pensados para
> copiarse y ejecutarse **a mano**, uno a uno, con comprobación entre pasos.

---

## 0. BLOQUEANTES DETECTADOS EN C5 (el stack está CAÍDO)

State real del KICKPI en el momento de la auditoría C5:

```
$ systemctl is-active docker
inactive                     ← el daemon de Docker NO está corriendo
$ systemctl is-enabled docker
enabled                      ← debería arrancar solo al iniciar
$ curl http://127.0.0.1:8888/health
(sin respuesta)              ← el agente no escucha: NO hay contenedores
$ curl https://kickpi-jarvis.tail1e3b4d.ts.net/health
(sin respuesta)              ← Tailscale Serve responde, pero sin nadie detrás
$ ls -la /mnt/data
vacío                        ← el disco externo NO está montado
$ grep sda /etc/fstab
UUID=5ad6d453-…  /mnt/data  ext4  defaults,noatime  0  2   ← sí está en fstab
```

**Es decir: las tres URLs de Jarvis están caídas ahora mismo**, por dos
motivos independientes: el daemon de Docker está parado y el disco externo
no está montado.

**Consecuencias que importan:**

1. El compose monta `/mnt/data/docker/executor_ssh:/app/ssh:ro`. Con el disco
   sin montar, Docker crearía directorios **vacíos** en la eMMC y el
   ejecutor **se quedaría sin la clave SSH dedicada ni el `known_hosts**:
   el multi-host dejaría de funcionar. La clave real del ejecutor no está en
   ningún otro sitio del sistema.
2. `/mnt/data/docker/executor_data` (auditoría del ejecutor) y
   `/mnt/data/docker/jarvis_data` (sesiones y auditoría del agente) también
   están en el disco: **reconstruir con el disco desmontado arrancaría con
   datos vacíos y perdería la auditoría**. Los datos NO se han perdido: están
   en el disco, inaccesibles hasta montarlo.

### Paso 0 (obligatorio, lo hace el administrador)

```bash
# en el KICKPI, con la contraseña sudo ROTADA (ver punto 2)

# 0.1) Montar el disco externo
sudo mount /mnt/data
df -h /mnt/data                       # debe verse el disco de 698 GB
ls -la /mnt/data/docker/executor_ssh/ # id_ed25519_macbook + known_hosts_macbook
ls -la /mnt/data/docker/executor_data/  # audit.db
ls -la /mnt/data/docker/jarvis_data/    # jarvis.db

# 0.2) Levantar Docker
sudo systemctl start docker
systemctl is-active docker            # debe decir: active
sudo docker info --format '{{.ServerVersion}}'

# 0.3) Ver el estado del stack (antes de tocar nada)
cd /home/leandro/jarvis
sudo docker compose ps
```

> Si `mount` se niega: `sudo mount -a`, o `sudo systemctl daemon-reload &&
> sudo mount /mnt/data`. Si el UUID ha cambiado: `sudo blkid` y corregir
> `/etc/fstab`.
>
> Si Docker no arranca, mirar `sudo journalctl -u docker -n 60 --no-pager`
> antes de forzar nada: con el disco sin montar, los volúmenes del compose
> son rutas inexistentes y el daemon puede quejarse.


---

## 1. Copia de seguridad verificable (ANTES de tocar nada)

```bash
# en el KICKPI
TS=$(date +%Y%m%d-%H%M%S)
DEST=/home/leandro/respaldos/antes-fase-c-$TS
sudo mkdir -p "$DEST"

# Bases de datos SQLite (copia consistente con la API de backup de SQLite)
sudo sqlite3 /mnt/data/docker/executor_data/audit.db ".backup '$DEST/audit.db'"
sudo sqlite3 /mnt/data/docker/jarvis_data/jarvis.db  ".backup '$DEST/jarvis.db'"

# Configuracion y material criptografico
sudo cp /home/leandro/jarvis/.env                "$DEST/env"
sudo cp /home/leandro/jarvis/docker-compose.yml  "$DEST/docker-compose.yml"
sudo cp -r /home/leandro/jarvis/executor/config  "$DEST/executor-config"
sudo cp -r /mnt/data/docker/executor_ssh         "$DEST/executor_ssh"

# Comprobacion: los archivos copiados deben ser legibles y coherentes
sudo chmod -R go-rwx "$DEST"; sudo chown -R leandro:leandro "$DEST"
sha256sum "$DEST"/*.db "$DEST"/env | tee "$DEST/SHA256SUMS"
sqlite3 "$DEST/audit.db" "PRAGMA integrity_check;"   # debe decir: ok
sqlite3 "$DEST/jarvis.db"  "PRAGMA integrity_check;"   # debe decir: ok
tar czf /home/leandro/respaldos/antes-fase-c-$TS.tgz -C /home/leandro/respaldos "antes-fase-c-$TS"
ls -la /home/leandro/respaldos/antes-fase-c-$TS.tgz
```

**Criterio de aceptación:** `integrity_check` = `ok` en las dos bases, el
`.tgz` existe y `sha256sum -c` verifica.

---

## 2. Rotación de la contraseña sudo (la que se filtró)

La contraseña `NuevaPassSegura2024!` quedó expuesta en el chat: **debe
cambiarse**. Es una acción del administrador; el asistente no la ejecuta ni
la guarda.

```bash
# en el KICKPI (interactivo)
sudo passwd leandro
# se pide la contraseña NUEVA dos veces; no se escribe en ningún sitio
```

Verificación (no expone la contraseña):

```bash
sudo -v                                    # pide la nueva contraseña
sudo -k true && echo "sudo operativo"       # tras un tiempo, revalida
```

> El estado actual del `sudo` en el board es **roto**: el journal muestra
> `pam_unix(sudo:auth): authentication failure` pese a que la cuenta está
> sana (`passwd -S leandro` → `P 2026-10-19`, sin caducidad, sin sudoers
> propio). La causa más probable es la **corrupción por pegado** del
> portapapeles (`^[[200~`). Si la nueva contraseña sigue fallando, teclear
> la contraseña a mano (sin pegar) o usar la consola física del KICKPI.

---

## 3. Tokens independientes (generación segura)

Dos tokens **distintos** y aleatorios. El de aprobaciones es el único que
comparte agente y ejecutor.

```bash
# en el KICKPI (no se escriben en el chat ni en el historial del shell)
APROB=$(python3 -c "import secrets; print(secrets.token_urlsafe(48))")
EJEC=$(python3 -c "import secrets; print(secrets.token_urlsafe(48))")
# Comprobacion de entropia minima (longitud y variedad de caracteres)
python3 - "$APROB" "$EJEC" <<'PY'
import sys
for nombre, valor in (("aprobaciones", sys.argv[1]), ("ejecutor", sys.argv[2])):
    assert len(valor) >= 40, f"{nombre}: demasiado corto"
    assert len(set(valor)) >= 20, f"{nombre}: poca variedad"
    print(f"{nombre}: OK ({len(valor)} caracteres)")
PY
```

**Asignación (en `/home/leandro/jarvis/.env`):**

| Variable | Servicio | Valor |
|---|---|---|
| `JARVIS_EXECUTOR_APPROVALS_TOKEN` | ejecutor | `APROB` |
| `JARVIS_APPROVALS_TOKEN` | agente | `APROB` (**el mismo**) |
| `JARVIS_EXECUTOR_TOKEN` | ejecutor | `EJEC` (el actual, sin cambios) |

El agente y el ejecutor **comparten** el token de aprobaciones porque el
agente actúa como proxy autenticado de la PWA. El token del ejecutor
(`X-Executor-Token`) **no** se comparte con el agente para las rutas de
aprobación: son superficies distintas a propósito.

---

## 4. Verificación de variables y compose (SIN desplegar todavía)

```bash
cd /home/leandro/jarvis
# 1) ¿Falta alguna variable obligatoria? (el compose aborta si falta)
grep -oE '\$\{[A-Z_]+:?\?[^}]*\}' docker-compose.yml | sort -u
# 2) Comprobar que las variables existen SIN imprimir sus valores
for v in JARVIS_EXECUTOR_TOKEN JARVIS_EXECUTOR_APPROVALS_TOKEN JARVIS_APPROVALS_TOKEN; do
  grep -qE "^${v}=." .env && echo "$v: definida" || echo "$v: FALTA"
done
# 3) Permisos del .env (contiene secretos)
ls -l .env            # debe ser -rw------- leandro leandro
# 4) Sintaxis del compose
docker compose config --quiet && echo "compose valido"
# 5) ¿Que imagen se desplegaria? (sin aplicarla)
docker compose config --images
```

---

## 5. Rebuild controlado (solo con el disco montado)

```bash
# 0) Confirmar que el disco está montado (punto 0)
df -h /mnt/data | tail -1

# 1) Parar SOLO los dos servicios afectados (el resto sigue en pie)
sudo docker compose stop jarvis-agent jarvis-executor

# 2) Reconstruir (el agente compila la PWA dentro de la imagen)
sudo docker compose build jarvis-executor jarvis-agent

# 3) Levantar
sudo docker compose up -d --no-deps jarvis-executor jarvis-agent

# 4) Estado
sudo docker compose ps
sudo docker compose logs --tail=40 jarvis-executor jarvis-agent
```

---

## 6. Health checks

```bash
# Desde el KICKPI (los puertos son internos del compose)
sudo docker compose exec jarvis-executor python3 -c "
import urllib.request, json
print(json.load(urllib.request.urlopen('http://127.0.0.1:8000/health')))"

# Desde el MacBook, por la tailnet
curl -fsS https://kickpi-jarvis.tail1e3b4d.ts.net/health | python3 -m json.tool
curl -fsS -o /dev/null -w "PWA %{http_code}\n" https://kickpi-jarvis.tail1e3b4d.ts.net/
```

Valores esperados:

| Comprobación | Valor esperado |
|---|---|
| `/health` del agente | `"status": "ok"`, `"pwa": true` |
| `/health` del ejecutor | `"status": "ok"`, `token_configured: true` |
| `GET /operations` (token del ejecutor) | incluye `host_git_diff_stat` con `requires_approval: true` |
| `GET /approvals` (token de aprobaciones) | 200 y `[]` (sin pendientes) |
| `GET /approvals` con el **token del ejecutor** | **401** (superficies separadas) |

---

## 7. Prueba E2E real con `host_git_diff_stat`

```bash
# 7.1) El LLM pide la operación (como en el chat): NO se ejecuta nada
curl -s -X POST http://172.19.0.2:8000/exec \
  -H "X-Executor-Token: $JARVIS_EXECUTOR_TOKEN" -H 'Content-Type: application/json' \
  -d '{"host":"macbook","operation":"host_git_diff_stat","params":{"path":"/home/leandro/kickpi-ai"}}' \
  | python3 -m json.tool
# esperado: "state": "pending_approval" y un "approval": {"id": "..."}

# 7.2) El LLM intenta aprobar por su cuenta: 401
curl -s -o /dev/null -w "%{http_code}\n" -X POST \
  http://172.19.0.2:8000/approvals/<ID>/approve \
  -H "X-Executor-Token: $JARVIS_EXECUTOR_TOKEN" -H 'Content-Type: application/json' \
  -d '{"approved_by":"el-llm"}'

# 7.3) En el iPhone: PWA → Aprobaciones → Aprobar → Confirmar y ejecutar
#      (la PWA encadena aprobar + ejecutar)
```

---

## 8. Validación de auditoría y bloqueo de replay

```bash
# 8.1) La auditoría del ejecutor: una fila por acción
sudo docker compose exec jarvis-executor python3 -c "
import sqlite3
c = sqlite3.connect('/data/audit.db'); c.row_factory = sqlite3.Row
for r in c.execute('SELECT created_at, host_id, operation, allowed, ok, exit_code, params_json FROM executions ORDER BY id DESC LIMIT 6'):
    print(dict(r))"

# 8.2) La auditoría del agente: la decisión humana
sudo docker compose exec jarvis-agent python3 -c "
import sqlite3
c = sqlite3.connect('/data/jarvis.db'); c.row_factory = sqlite3.Row
for r in c.execute(\"SELECT created_at, tool, arguments, ok, summary FROM audit WHERE tool LIKE 'aprobacion%' ORDER BY id DESC LIMIT 6\"):
    print(dict(r))"

# 8.3) Replay: la misma aprobación no se ejecuta dos veces → 409
curl -s -o /dev/null -w "%{http_code}\n" -X POST \
  http://172.19.0.2:8000/approvals/<ID>/execute \
  -H "X-Approvals-Token: $JARVIS_EXECUTOR_APPROVALS_TOKEN"
# esperado: 409
```

En la fila de auditoría del ejecutor debe aparecer `allowed=1` **una sola
vez** para esa operación, con `approval_id` dentro de `params_json`.

---

## 9. Rollback

```bash
# 9.1) Volver a las imagenes anteriores
sudo docker compose down
sudo docker image ls | grep jarvis          # anotar los tags previos
sudo docker compose up -d --no-deps jarvis-executor jarvis-agent   # con los tags previos en el .env

# 9.2) Si el problema es de datos, restaurar la copia
sudo systemctl stop docker
sudo cp /home/leandro/respaldos/antes-fase-c-<TS>/audit.db  /mnt/data/docker/executor_data/audit.db
sudo cp /home/leandro/respaldos/antes-fase-c-<TS>/jarvis.db /mnt/data/docker/jarvis_data/jarvis.db
sudo chown $(id -u):$(id -g) /mnt/data/docker/executor_data/audit.db /mnt/data/docker/jarvis_data/jarvis.db
sudo systemctl start docker
sudo docker compose up -d

# 9.3) Si solo falla la parte de aprobaciones, se puede dejar el agente
#      sin token de aprobaciones: /api/aprobaciones responderá 503 y el
#      resto de la PWA sigue funcionando.
```

**Punto de retorno seguro:** el cambio es reversible sin tocar la BD
(el esquema `approvals` y `session_subjects` se crean con
`CREATE TABLE IF NOT EXISTS` y no estorban a la versión anterior).

---

## 10. Resumen de la secuencia

| # | Paso | Quién | Bloqueante |
|---|---|---|---|
| 0 | Montar `/mnt/data`, arrancar Docker, verificar las claves | usuario (sudo) | **sí** |
| 1 | Copia de seguridad verificable | usuario | no |
| 2 | Rotar la contraseña sudo | usuario | no |
| 3 | Generar los tokens | usuario | no |
| 4 | Verificar `.env` y compose | cualquiera | no |
| 5 | Rebuild de agente y ejecutor | usuario (sudo) | requiere 0–4 |
| 6 | Health checks | cualquiera | no |
| 7 | E2E con `host_git_diff_stat` | usuario (iPhone) | requiere 5 |
| 8 | Auditoría y replay | cualquiera | requiere 7 |
| 9 | Rollback (solo si falla) | usuario (sudo) | — |

---

## 11. Estado del stack en la auditoría C5 (2026-10-20)

| Comprobación | Resultado |
|---|---|
| `systemctl is-active docker` | `inactive` |
| `systemctl is-enabled docker` | `enabled` |
| `/mnt/data` | **sin montar** (aparece en `fstab`) |
| `GET /health` (agente, tailnet) | sin respuesta |
| `GET /health` (agente, loopback) | sin respuesta |
| Contenedores | no se pueden listar sin `sudo` (socket restringido) |
| `.env` y `docker-compose.yml` | presentes y con permisos correctos (`.env` en `-rw-------`) |

**El stack de Jarvis está caído.** El despliegue de la Fase C debe empezar
por el paso 0 (montar el disco y levantar Docker); hasta entonces, cualquier
`docker compose up --build` crearía volúmenes vacíos en la eMMC y perdería
la auditoría.
