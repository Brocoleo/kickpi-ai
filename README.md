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
| `userpatches/` | Intentos previos con Armbian (obsoletos para esta placa). |

## Fases

- **F1** — auditoría del portátil: Mint 22.3, 104 GB libres, Docker OK, solo WiFi
  por hotspot del iPhone (172.20.10.0/28), sin Ethernet.
- **F2** — REV2 confirmada → el WiFi exige el builder de pyavitz (kernel 6.12.109).
- **F3** — build en la nube (GitHub Actions) + descarga verificada del `.img.xz`.
- **F4** — grabar el pendrive y, desde la placa, `dd` a la eMMC (`/dev/mmcblk2`).
- **F5** — arrancar desde eMMC (sin SD) y comprobar WiFi/SSH.

## Uso

```bash
# 1) Compilar en la nube: Actions -> "Build K2B V2 Debian (SWT6621S)" -> Run workflow
#    (deja todos los valores por defecto; SSID/clave ya vienen grabados)

# 2) En cualquier PC: descargar el release y verificar SHA256
./scripts/download-image.sh "<url-del-.img.xz>"

# 3) Grabar el pendrive (se pide confirmación explícita escribiendo el dispositivo)
./scripts/make-pendrive.sh dist/<imagen>.img.xz /dev/sdX

# 4) En la placa, desde el sistema actual: instalar en la eMMC
sudo ./k2b-emmc-install.sh /media/usb/<imagen>.img.xz
#    apagar, quitar el pendrive Y la SD, y arrancar: ya corre desde eMMC.
```

## Datos del sistema final

- usuario `leandro` (contraseña `1321`, `sudo` pide contraseña), hostname `kickpi-k2b-v2`
- WiFi `iPhone de Leandro`, país `CL`, NetworkManager, MAC de wlan0 estable
- TZ `America/Santiago`
- firmware SWT6621S en `/lib/firmware` con los nombres exactos que pide el driver
