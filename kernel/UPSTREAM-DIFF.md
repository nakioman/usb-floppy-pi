# Upstream provenance

These files are forked from the Linux kernel **v6.12.75** for Phase 2 of the
usb-floppy-pi project. They will be modified to support UFI subclass, speed
throttling, and a kernel-side buzzer.

## Source files

| Our file               | Upstream path                                              |
|------------------------|-----------------------------------------------------------|
| `f_floppy.c`           | `drivers/usb/gadget/function/f_mass_storage.c`            |
| `f_floppy.h`           | `drivers/usb/gadget/function/f_mass_storage.h`            |
| `storage_common.c`     | `drivers/usb/gadget/function/storage_common.c`            |
| `storage_common.h`     | `drivers/usb/gadget/function/storage_common.h`            |
| `g_floppy_main.c`      | `drivers/usb/gadget/legacy/mass_storage.c`                |

## License

GPL-2.0 (same as upstream).

## Re-fetching

Run `kernel/scripts/fetch-upstream.sh` to re-pull the same versions.
This will OVERWRITE local modifications; back up first if needed.

## Modifications log

(Each subsequent task will append a one-line entry here describing its
change vs upstream.)

- 2026-05-07: Initial fork from Linux v6.12.75, no modifications yet.
