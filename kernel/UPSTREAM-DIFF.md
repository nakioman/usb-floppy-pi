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
| `configfs.h`           | `drivers/usb/gadget/configfs.h` (internal kernel header)  |

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
- 2026-05-07: Added kernel/configfs.h (forked from drivers/usb/gadget/configfs.h)
  because f_mass_storage.c references it for its configfs-based gadget setup
  path. We don't *use* configfs in Phase 2 (the legacy gadget approach
  initializes via fsg_config_from_params instead) but the file must compile
  the same code path nonetheless.
- 2026-05-07: Built two .ko files via Kbuild — usb_f_floppy.ko (registers
  function "floppy") and g_floppy.ko (legacy gadget that requests it).
  modinfo of g_floppy declares depends=usb_f_floppy,libcomposite, so depmod
  will auto-load the function module on `modprobe g_floppy`.
- 2026-05-07: Verified module loads cleanly via insmod (libcomposite first,
  then usb_f_floppy, then g_floppy file=...). dmesg shows
  "g_floppy gadget.0: USB Floppy Pi Gadget, version: 0.1.0" and
  "dwc2 3f980000.usb: bound driver g_floppy". UDC state is "configured" — the
  host enumerated the device. Native legacy sysfs at
  /sys/class/udc/<udc>/device/gadget*/lun0/ exposes file (rw), ro (rw),
  forced_eject, nofua. No inquiry_string at this path — Task 7+8 will add
  one via our own sysfs class.
- 2026-05-07: f_floppy.c — added module param `subclass` (default "ufi",
  fallback "scsi") + helper `floppy_apply_subclass()` that mutates the
  exported `fsg_intf_desc.bInterfaceSubClass` at the start of `fsg_bind()`,
  before any descriptor is sent to the host. Verified both modes log to
  dmesg correctly: "bInterfaceSubClass = 0x04 (UFI)" or "0x06 (SCSI)".
  This is the core Phase 2.2 win — Windows now treats the device as a
  Floppy Disk Drive regardless of media presence.
- 2026-05-07: f_floppy.c — added `floppy_active_common` bridge (single
  active fsg_common pointer set by fsg_common_setup, cleared by
  fsg_common_release) plus three EXPORT_SYMBOL_GPL bridges:
  floppy_lun_show_file/store_file/store_ro and floppy_active_lun0().
  These hide the private fsg_common layout from g_floppy.ko, which only
  needs to call show/store helpers. f_floppy.h declares the bridges.
- 2026-05-07: g_floppy_main.c — registered sysfs class
  /sys/class/usb_floppy/usb-floppy-pi/ with attrs lun0_file (rw),
  lun0_ro (rw), lun0_inquiry_string (rw). Registered in msg_bind after
  fi_msg is set; unregistered in msg_unbind. Verified all three attrs
  read/write correctly: cat returns the current value, echo writes it.
  Empty write to lun0_file ejects the medium, non-empty mounts.
