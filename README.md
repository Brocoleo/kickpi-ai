# KICKPI AI — Servidor doméstico (K2B V2)

Placa: KICKPI K2B V2 / Allwinner H618 / 4GB DDR3 (a verificar) / 32GB eMMC.
Objetivo: Armbian Minimal CLI headless 24/7 (Docker, NAS USB, PriceHunter, Telegram, Tailscale, MQTT).

## Estructura portable
Este repo se clona igual en laptop (prueba inicial SD) y en PC estacionario.
- `.github/workflows/build-armbian.yml` — compila en la nube (vía B), evita gastar hotspot en build local.
- `scripts/` — descarga/verificación y flasheo (Fases 3-4).
- `docs/` — bitácora por fases.

## Fases
- F1: auditoría Mint 22.3 OK (104GB libres, Docker OK, solo WiFi iPhone 172.20.10.5/28, sin Ethernet).
- F2: V2 confirmada. Armbian V2 solo publica Xfce 809MB; Minimal se compila. Se elige vía B GitHub Actions `kickpi-k2b-v2 / trixie / minimal / current`.
- F3: build en nube + descarga `.img.xz` + `.sha` + verificación.
- F4: flasheo a `/dev/sdb` (SD 14,6G) con confirmación explícita.

## Uso
```bash
# En cualquier PC:
git clone https://github.com/Brocoleo/kickpi-ai.git
cd kickpi-ai
./scripts/download-verify.sh "<url-img.xz>" "<url-sha>"
# Solo en PC con lector SD y con confirmación:
./scripts/flash.sh
```
