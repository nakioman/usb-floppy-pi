# usb-floppy-pi

USB floppy drive emulator for Raspberry Pi Zero 2W. Connects to a retro PC via USB and presents itself as a 1.44 MB floppy drive (`A:` via BIOS legacy emulation, no host drivers required).

Designed for a 2010-era PC running DOS / Win98 SE dual-boot, but works as a generic USB floppy on any host.

## Status

**Phase 1 — USB Mass Storage gadget (configfs):** ✅ shipped
**Phase 2 — Custom kernel module + speed throttle + Floppy identity:** ✅ shipped
**Phase 2.4 — Buzzer audio:** ⏳ deferred (separate sub-plan)

✅ USB Mass Storage gadget — out-of-tree fork (`g_floppy`, `usb_f_floppy`)
   that always identifies as a 1.44 MB Floppy Disk Drive (Windows shows `A:`
   even with no media)
✅ Configurable speed throttle: `floppy-real` (~50 KB/s read, 30 KB/s
   write, 6 ms seek), `floppy-fast`, `unthrottled` — switchable from web UI
✅ Web UI for browsing, mounting, ejecting, uploading + hardware controls
   (speed preset, volume placeholder, buzzer placeholder)
✅ Samba share for drag-and-drop image management from any machine on the LAN
✅ `.img`/`.ima`/`.imz` upload formats (auto-normalized to `.img`)
✅ Last-mounted image restored on boot; `blank.img` fallback on eject so
   Windows never re-classifies the device as a generic USB drive
✅ Read-only and session mount modes
✅ DKMS-packaged kernel module — survives apt kernel upgrades

⏳ LCD + physical buttons + buzzer audio — Phase 2.4 / Phase 3

## Hardware

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

The installer is **idempotent** — safe to re-run after upgrades or partial
installs. It:

- installs the `g_floppy` + `usb_f_floppy` kernel modules via DKMS (so they
  rebuild automatically on every kernel upgrade),
- writes `/etc/modules-load.d/usb-floppy-pi.conf` and
  `/etc/modprobe.d/usb-floppy-pi.conf` so the modules auto-load at boot
  with the right parameters,
- limits DKMS parallelism to `-j1` via `/etc/dkms/framework.conf.d/`
  (Pi Zero 2W's 512 MB RAM OOMs on the default `-j$(nproc)`),
- creates `/var/lib/usb-floppy-pi/blank.img` (a pre-formatted FAT12 image)
  and a `current.img` symlink the Python service repoints when you mount
  / eject — guarantees Windows always classifies the device as a Floppy.

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

The configfs gadget backend (`src/usb_floppy_pi/gadget/configfs_backend.py`)
and the SysfsBackend (`src/usb_floppy_pi/gadget/sysfs_backend.py`) are
verified manually on the Pi.

The kernel module lives in `kernel/` (`f_floppy.c`, `g_floppy_main.c`,
`storage_common.c`, `floppy_throttle.c`). It's an out-of-tree fork of
`f_mass_storage` patched to always advertise a Floppy Disk Drive identity
and to throttle reads/writes/seeks via `floppy_throttle.{c,h}`. Build it
locally with:

```bash
make -C kernel KDIR=/lib/modules/$(uname -r)/build
```

(but on the Pi, just re-run `deploy/install.sh` — DKMS handles it.)

## Spec & plans

- Spec: `docs/superpowers/specs/2026-05-06-usb-floppy-pi-design.md`
- Phase 1 plan: `docs/superpowers/plans/2026-05-06-phase-1-mvp-headless.md`
- Phase 2 plan: `docs/superpowers/plans/2026-05-07-phase-2-kernel-module.md`
