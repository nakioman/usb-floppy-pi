# `g_floppy.ko` — usb-floppy-pi kernel module

USB Mass Storage gadget specialised for floppy emulation. Replaces the upstream
`g_mass_storage` for our use case. Adds:

- Configurable speed throttling — three named presets (`floppy-real`,
  `floppy-fast`, `unthrottled`) controlling read/write KB/s and seek-time
  delay between tracks. Implemented as bedded delays in the FSG kthread's
  `do_read` / `do_write` paths.
- Sysfs class `/sys/class/usb_floppy/usb-floppy-pi/` for runtime control
  from userspace (the Python web UI consumes this).
- Module param `subclass=ufi|scsi` (default scsi) — UFI is a stub for
  future Phase 4 work; the upstream `f_mass_storage` doesn't implement
  UFI command set so Windows rejects the device.

## Layout

```
kernel/
├── Makefile                # Kbuild out-of-tree, builds two .ko files
├── dkms.conf               # DKMS config (AUTOINSTALL=yes)
├── README.md               # this file
├── UPSTREAM-DIFF.md        # tracked diff vs Linux 6.12.75 upstream
├── scripts/
│   └── fetch-upstream.sh   # one-shot pull of source files from kernel.org
├── f_floppy.{c,h}          # FORKED from drivers/usb/gadget/function/f_mass_storage.{c,h}
├── storage_common.{c,h}    # FORKED, mostly unchanged
├── g_floppy_main.c         # FORKED from drivers/usb/gadget/legacy/mass_storage.c
├── configfs.h              # FORKED from drivers/usb/gadget/configfs.h (kernel-internal)
├── floppy_throttle.{c,h}   # NEW: rate-limiting hooks
└── floppy_buzzer.{c,h}     # NEW (Phase 2.4): PWM buzzer driver — not yet present
```

## Two .ko files

The build produces:

| Module | Role |
|--------|------|
| `usb_f_floppy.ko` | Registers the USB gadget function `floppy` via `usb_function_register`. Owns the FSG state machine, throttle, and (Phase 2.4) buzzer. |
| `g_floppy.ko`     | Single-purpose gadget that calls `usb_get_function_instance("floppy")` at bind time. Exposes the `/sys/class/usb_floppy` class. |

Module dependencies (set by `MODULE_SOFTDEP`/`depends` at link time):
`g_floppy → usb_f_floppy → libcomposite`. So `modprobe g_floppy` resolves
the chain automatically.

## Build out-of-tree (manual, for development)

```bash
cd kernel
make KDIR=/lib/modules/$(uname -r)/build
sudo modprobe libcomposite
sudo insmod ./usb_f_floppy.ko
sudo insmod ./g_floppy.ko file=/path/to/disk.img stall=0 removable=1 \
                          speed_preset=floppy-real
```

## Install as DKMS (production)

```bash
sudo apt install dkms linux-headers-$(uname -r)
sudo cp -r kernel /usr/src/g-floppy-0.1.0
sudo dkms add -m g-floppy -v 0.1.0
sudo dkms build -m g-floppy -v 0.1.0
sudo dkms install -m g-floppy -v 0.1.0
```

After install, `modprobe g_floppy` works system-wide and the module
auto-rebuilds on every apt-managed kernel upgrade (see DKMS triggers).

To boot the module automatically and pass parameters, drop the configs
under `deploy/modules-load/usb-floppy-pi.conf` and
`deploy/modprobe/usb-floppy-pi.conf` (Phase 2.7 task — install.sh
handles that).

## Module parameters

`usb_f_floppy` params:

| param | default | description |
|-------|---------|-------------|
| `subclass` | `scsi` | `scsi` (works on all hosts) or `ufi` (experimental, Windows rejects) |

`g_floppy` params (most are passed straight to the FSG core):

| param | default | description |
|-------|---------|-------------|
| `file` | (empty) | Path(s) to backing image file(s); empty = no medium |
| `ro`   | `0`     | Read-only flag |
| `removable` | `1`  | Removable media flag (must be 1 for floppy emulation) |
| `stall` | `1`    | Bulk endpoint stall — set to `0` for Windows (Code 10 issue) |
| `nofua` | `0`    | Don't honour FUA — `0` = honour it (better data integrity) |
| `speed_preset` | `floppy-real` | Initial speed preset |

## Sysfs runtime interface

`/sys/class/usb_floppy/usb-floppy-pi/`:

| attr | rw | description |
|------|----|-------------|
| `lun0_file` | rw | Backing file path; empty write = eject |
| `lun0_ro` | rw | Read-only flag (kernel rejects change while file attached) |
| `lun0_inquiry_string` | rw | SCSI INQUIRY string (28 chars: 8 vendor + 16 product + 4 rev) |
| `speed_preset` | rw | `floppy-real` / `floppy-fast` / `unthrottled` |
| `speed_read_kbps` | ro | Derived |
| `speed_write_kbps` | ro | Derived |
| `seek_us` | ro | Derived seek-time delay |

(Phase 2.4 will add `volume`, `mute`, `buzzer` for the buzzer.)

## Source provenance

See `UPSTREAM-DIFF.md` for which upstream files we forked and what changes
we made on top of them. Re-fetch the same upstream versions with
`scripts/fetch-upstream.sh`.

## License

GPL-2.0 (inherited from upstream).
