# usb-floppy-pi

USB floppy drive emulator for Raspberry Pi Zero 2W. Connects to a retro PC via USB and presents itself as a 1.44 MB floppy drive (`A:` via BIOS legacy emulation, no host drivers required).

Designed for a 2010-era PC running DOS / Win98 SE dual-boot, but works as a generic USB floppy on any host.

## Phase 1 status

✅ USB Mass Storage gadget (configfs)
✅ Web UI for browsing, mounting, ejecting, uploading
✅ Samba share for drag-and-drop image management from any machine on the LAN
✅ `.img`/`.ima`/`.imz` upload formats (auto-normalized to `.img`)
✅ Last-mounted image restored on boot
✅ Read-only and session mount modes

⏳ LCD + buttons + buzzer audio — Phase 2/3 (separate plans)

## Hardware (Phase 1)

- Raspberry Pi Zero 2W
- microSD ≥ 8 GB
- USB-A ↔ micro-USB **data** cable (not charge-only)

## Install

1. Flash Raspberry Pi OS Lite (Bookworm 64-bit) with `rpi-imager`. In the imager's advanced settings, enable SSH and configure WiFi.
2. SSH into the Pi.
3. Clone this repo and run the installer:

```bash
git clone <repo-url> usb-floppy-pi
cd usb-floppy-pi
sudo ./deploy/install.sh
```

4. Reboot: `sudo reboot`

## Use

- Connect the Pi's micro-USB **data** port (the one closer to the HDMI port — the other is power-only) to the retro PC's USB.
- Connect the Pi's power port to a 5V power source — or, if you accept the small risk of corruption on PC power-off, you can power the Pi from the same data cable (see spec §5.6).
- From any device on the LAN: open `http://floppy.local` for the web UI.
- From any device: mount `\\floppy\floppies` for drag-and-drop image management.

## Layout

```
/home/pi/floppies/
├── DOS 6.22/                 ← each subdirectory is one "set"
│   ├── ro                    ← optional marker file = whole set is read-only
│   ├── DISK001.img
│   └── DISK002.img
└── Win98 Boot/
    └── boot.img
```

When a set has more than one disk, the web UI lets you swap between disks (useful for multi-disk installers).

## Development

Run the test suite (cross-platform, no Pi needed):

```bash
python -m venv .venv
source .venv/bin/activate          # or .venv\Scripts\activate on Windows
pip install -e ".[dev]"
pytest -v
```

The configfs gadget backend (`src/usb_floppy_pi/gadget/configfs_backend.py`) is verified manually on the Pi (see Task 22 of the Phase 1 plan).

## Spec & plans

- Spec: `docs/superpowers/specs/2026-05-06-usb-floppy-pi-design.md`
- Phase 1 plan: `docs/superpowers/plans/2026-05-06-phase-1-mvp-headless.md`
