# KICKPI AI — Servidor doméstico (K2B V2)

Placa: KICKPI K2B V2 (REV 2.2, DDR3 4GB) / Allwinner H618 / 32GB eMMC.
WiFi/BT: **Seekwave SWT6621S** (SDIO).
Objetivo: Debian minimal headless 24/7 en la eMMC, accesible por WiFi (Docker,
NAS USB, PriceHunter, Telegram, Tailscale, MQTT).

## Por qué Debian (y no Armbian) en esta placa

El chip WiFi de la REV2 (SWT6621S) **no está en Armbian**:

- `linux-sunxi64-current/edge/legacy` no trae `CONFIG_WLAN_VENDOR_SWT6621S`
  (solo `UWE5622`/`SC2355`), ni los módulos `swt6621s_wifi` / `skw_sdio_lite`
  / `skwbt`, ni el PHY Ethernet `MAE0621A`.
- En 6.18 el driver del vendedor ni siquiera compila (variable `host` sin usar).

La ruta que sí funciona (verificada en el foro de Armbian por otro usuario con la
misma REV 2.2) es construir con **`pyavitz/debian-image-builder`** y kernel
**6.12.109**, que sí parchea el kernel con el driver del vendedor.

## Estructura

| Ruta | Qué es |
|---|---|
| `.github/workflows/build-kickpi-debian.yml` | Compila la imagen en la nube (uboot + kernel 6.12.109 + rootfs Debian trixie + firmware del SWT6621S) y publica un release descargable. |
| `scripts/gen-builder-config.sh` | Genera la configuración no-interactiva del builder (usuario, hostname, red, TZ...). |
| `scripts/fetch-swt6621s-firmware.sh` | Descarga y verifica (SHA256) el firmware del vendedor como respaldo. |
| `scripts/customize-image.sh` | Mónta la imagen y arregla el firmware + WiFi + MAC estable + sudo. |
| `scripts/download-image.sh` | Descarga el `.img.xz` del release y verifica su SHA256. |
| `scripts/verify-image.sh` | Comprueba la imagen (firmware, módulos, red, arranque) antes de grabarla. |
| `scripts/make-pendrive.sh` | Prepara el pendrive (modo `files` con instalador, o `raw` arrancable). |
| `scripts/k2b-emmc-install.sh` | Se ejecuta EN LA PLACA: clona la imagen al eMMC y verifica. |
| `scripts/k2b-postinstall.sh` | Se ejecuta EN LA PLACA ya arrancada: actualización, ufw, fail2ban y Docker opcional. |
| `userpatches/` | Intentos previos con Armbian (obsoletos para esta placa). |

### El firmware del SWT6621S

El driver del vendedor pide estos nombres en `/lib/firmware` (comprobado en la
imagen ya construida):

| Nombre que pide el driver | Qué trae la imagen del builder |
|---|---|
| `SWT6621S_IRAM_SDIO.bin` | 356 KB |
| `SWT6621S_DRAM_SDIO.bin` | 193 KB |
| `SWT6621S_NV_SDIO.bin` | **enlace** -> `SWT6621S_NV_SDIO_SHARE.bin` (220 B) |
| `SEEKWAVE_NV_SWT6621S.bin` | **enlace** -> `SWT6621S_SEEKWAVE_R00000.bin` (2,3 KB) |
| `SWT6621S_SEEKWAVE_R00000/1/4000/4001.bin` | las 4 calibraciones conocidas |
| `sv6160lite.nvbin` (Bluetooth) | lo copia `customize-image.sh` desde `seekwave/` |

Los dos enlaces son **válidos** (el destino existe y tiene contenido), así que el
`request_firmware()` del kernel los resuelve sin problema; por eso
`verify-image.sh` los mide siguiendo el enlace (`stat -L`).

Aun así `customize-image.sh` repasa cada nombre y, si se encontrara un fichero
diminuto de texto (lo que pasa cuando el firmware se descarga *suelto* desde la
web en vez de clonarlo con git: los enlaces llegan aplanados), lo sustituye por
el blob real del respaldo verificado. Es una red de seguridad: en esta imagen no
hace falta tocar nada. DRAM/IRAM se dejan como vienen, que son las que funcionan
en esta revisión.

## Fases

- **F1** — auditoría del portátil: Mint 22.3, 104 GB libres, Docker OK, solo WiFi
  por hotspot del iPhone (172.20.10.0/28), sin Ethernet.
- **F2** — REV2 confirmada → el WiFi exige el builder de pyavitz (kernel 6.12.109).
- **F3** — build en la nube (GitHub Actions) + descarga verificada del `.img.xz`.
- **F4** — grabar el pendrive y, desde la placa, `dd` a la eMMC (`/dev/mmcblk2`).
- **F5** — arrancar desde eMMC (sin SD) y comprobar WiFi/SSH.
- **F6** — puesta a punto por SSH (actualización, cortafuegos, fail2ban, Docker).

## Uso

### 1) Compilar en la nube

GitHub → **Actions** → `Build K2B V2 Debian (SWT6621S)` → **Run workflow**.
Tarda ~40-60 min y publica un *release* con `*.img.xz` + `*.img.xz.sha256`.

> **Clave del WiFi.** El repo es público, así que la clave **no** se guarda en el
> YAML: define el secret `WIFI_PSK` en *Settings → Secrets and variables →
> Actions* (o escríbela en el campo `psk` al lanzar el workflow, que no se
> guarda). Si falta, el workflow se detiene en el primer paso en vez de
> construir una imagen sin WiFi. El SSID (`iPhone de Leandro`) sí está por
> defecto y no es secreto.

### 2) Descargar y verificar (en cualquier PC)

```bash
./scripts/download-image.sh latest              # o la URL del asset
```

Verifica el SHA256 antes de seguir; si no coincide, aborta.

### 3) Verificar el contenido de la imagen

```bash
xz -dk dist/<imagen>.img.xz
sudo ./scripts/verify-image.sh dist/<imagen>.img
```

Monta la imagen en solo lectura y comprueba firmware, módulos, red, usuario y
arranque. Debe terminar con `0 críticos`; si algún crítico falla, **no la grabes**.

### 4) Preparar el pendrive

```bash
# Recomendado: deja la imagen + instalador + LEEME en el pendrive (FAT32/exFAT)
sudo ./scripts/make-pendrive.sh dist/<imagen>.img.xz /dev/sdX
# Si el pendrive no tiene sistema de ficheros (se BORRA todo):
sudo ./scripts/make-pendrive.sh dist/<imagen>.img.xz /dev/sdX --format fat32
```

Pide escribir el nombre del dispositivo para confirmar, y verifica el checksum
de la copia. En el pendrive queda un `LEEME.txt` con los pasos exactos.

### 5) Instalar en la eMMC (en la placa, con monitor + teclado)

```bash
lsblk                                  # localiza el pendrive (p.ej. /dev/sda1)
sudo mkdir -p /mnt/usb && sudo mount /dev/sda1 /mnt/usb
cd /mnt/usb && sha256sum -c <imagen>.img.sha256
sudo bash /mnt/usb/k2b-emmc-install.sh /mnt/usb/<imagen>.img
```

El instalador comprueba que el destino no es el disco del sistema, desmonta sus
particiones, pide confirmación (`SI`), escribe con `dd conv=fsync` y **verifica
leyendo de vuelta** el SHA256. Después: `sudo poweroff`, quitar el pendrive y
**la SD**, y encender.

### 6) Primer arranque desde la eMMC

La raíz se amplía sola a los 32 GB del eMMC y se une al WiFi (por
`/boot/credentials.txt`). Se conecta con `ssh leandro@<ip>`
(la IP aparece en los dispositivos del hotspot).

### 7) Puesta a punto (fase 6, por SSH)

```bash
scp scripts/k2b-postinstall.sh leandro@<ip>:~
ssh leandro@<ip> 'sudo bash k2b-postinstall.sh --docker'
```

Comprueba el estado (raíz en la eMMC, `wlan0` con IP, Internet), fija hora y
hostname, actualiza el sistema, activa `ufw` (solo permite SSH), instala
`fail2ban` y, con `--docker`, Docker + compose. Es idempotente.

## Datos del sistema final

- usuario `leandro` (contraseña `1321`, `sudo` pide contraseña), hostname `kickpi-k2b-v2`
- WiFi `iPhone de Leandro`, país `CL`, NetworkManager, MAC de wlan0 estable
- TZ `America/Santiago`
- firmware SWT6621S en `/lib/firmware` con los nombres exactos que pide el driver
