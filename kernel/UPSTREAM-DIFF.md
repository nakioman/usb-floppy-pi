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
- 2026-05-07: Renamed forked references for self-consistency:
  - `#include "f_mass_storage.h"` → `#include "f_floppy.h"` in both .c files
  - `DECLARE_USB_FUNCTION_INIT(mass_storage, ...)` → `(floppy, ...)` in f_floppy.c
    (registers our function under name "floppy", which g_floppy_main.c looks
    up via `usb_get_function_instance("floppy")`)
  - Header guard `USB_F_MASS_STORAGE_H` → `USB_F_FLOPPY_H` in f_floppy.h
  - Interface string `"Mass Storage"` → `"USB Floppy"` (shown in lsusb)
  - g_floppy_main.c: DRIVER_DESC, DRIVER_VERSION, composite driver `.name`,
    `MODULE_DESCRIPTION` updated. composite driver name is now `g_floppy`.
  - f_floppy.c: MODULE_DESCRIPTION/MODULE_AUTHOR updated.
  - Comments referencing "mass storage" left as-is (historical context).
  - storage_common.c bInterfaceClass = USB_CLASS_MASS_STORAGE kept — that's
    the USB class constant 0x08 which is correct for floppy too. Only the
    SubClass changes (Task 6 will set it to USB_SC_UFI).
