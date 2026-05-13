# Phase 2 — Custom Kernel Module Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a custom Linux kernel module `g_floppy.ko` that replaces `f_mass_storage` for our Raspberry Pi USB floppy emulator, adding UFI subclass identification, speed throttling, and HW PWM buzzer audio — packaged as DKMS so it survives kernel updates, with the Phase 1 web UI extended to control all new features.

**Architecture:** Fork Linux kernel files (`f_mass_storage.c`, `storage_common.c`, `mass_storage.c` legacy) into our repo's `kernel/` directory, modify minimally (subclass change + a handful of hooks), add three new files (`floppy_throttle.c`, `floppy_buzzer.c`, `g_floppy_main.c`), package as DKMS module. Python web UI swaps from `ConfigFsBackend` to `SysfsBackend` that talks to `/sys/class/usb_floppy/`.

**Tech Stack:** C (kernel-mode, kernel 6.12 ABI), DKMS, Linux PWM subsystem, hrtimer, kthread_worker, Python 3.11+ (existing FastAPI), bash (install scripts).

**Spec reference:** `docs/superpowers/specs/2026-05-07-phase-2-kernel-module-design.md`

**Dev workflow note:** Almost all kernel testing happens via SSH to the dev Pi (hostname `floppyusb`, user `pi`, password `floppy`). The helper script `.pi-dev-helper.py` (uncommitted, in repo root) wraps SSH/SCP via paramiko. Commands like `python .pi-dev-helper.py run "cmd"` and `python .pi-dev-helper.py sudo "cmd"` are used throughout.

**TDD note:** Kernel modules can't be unit-tested in the traditional pytest sense. For C tasks, "test" means: build, load, check `dmesg` and `sysfs`, validate behavior, unload cleanly. For Python tasks, we keep the existing pytest-based TDD discipline.

---

## File Structure

### New: `kernel/` (forked + new code)

```
kernel/
├── Makefile                       # Kbuild out-of-tree
├── dkms.conf                      # DKMS config
├── README.md                      # build/install/debug notes
├── UPSTREAM-DIFF.md               # changes vs upstream Linux v6.12
├── scripts/
│   └── fetch-upstream.sh          # one-shot fetch of source files from kernel.org
├── f_floppy.c                     # FORKED from drivers/usb/gadget/function/f_mass_storage.c (modified)
├── f_floppy.h                     # FORKED from same path (renamed)
├── storage_common.c               # FORKED as-is
├── storage_common.h               # FORKED as-is
├── g_floppy_main.c                # FORKED from drivers/usb/gadget/legacy/mass_storage.c (renamed + customized)
├── floppy_throttle.c              # NEW: rate-limiting bulk transfers
├── floppy_throttle.h
├── floppy_buzzer.c                # NEW: PWM driver + sound engine
└── floppy_buzzer.h
```

### Modified: `src/usb_floppy_pi/` (Python)

- **New file:** `src/usb_floppy_pi/gadget/sysfs_backend.py` — implements `GadgetBackend` Protocol via `/sys/class/usb_floppy/` instead of configfs.
- **Modified:** `src/usb_floppy_pi/gadget/backend.py` — extend Protocol with optional `set_speed_preset`, `set_volume`, `set_mute`, `set_buzzer_enabled`, `get_metrics`.
- **Modified:** `src/usb_floppy_pi/__main__.py` — auto-detect `SysfsBackend` vs `ConfigFsBackend`, env override `USB_FLOPPY_BACKEND`.
- **Modified:** `src/usb_floppy_pi/core/config.py` — add `speed_preset`, `volume`, `mute`, `buzzer_enabled` fields.
- **Modified:** `src/usb_floppy_pi/web/api.py` — endpoints `POST /api/speed`, `/api/volume`, `/api/buzzer`, `/api/mute`.
- **Modified:** `src/usb_floppy_pi/web/static/index.html` — UI for new controls.
- **Modified:** `src/usb_floppy_pi/web/static/app.js` — handlers for new controls.

### Modified: `deploy/`

- **New:** `deploy/modules-load/usb-floppy-pi.conf` — `g_floppy` line for /etc/modules-load.d.
- **New:** `deploy/modprobe/usb-floppy-pi.conf` — `options g_floppy ...` for /etc/modprobe.d.
- **Modified:** `deploy/install.sh` — DKMS install + new configs + dtoverlay PWM line.
- **Modified:** `deploy/boot/config.txt.append` — add `dtoverlay=pwm,pin=18,func=2`.
- **Modified:** `deploy/boot/cmdline.txt.append` — drop `,libcomposite` (no longer needed).
- **Modified:** `deploy/systemd/usb-floppy-pi.service` — drop ExecStartPre for libcomposite/configfs.

---

## Task 1: Verify dev environment + install kernel build tools on Pi

This task validates we can build kernel modules on the Pi. No code changes yet.

**Files:** none (Pi-side only)

- [ ] **Step 1: Verify SSH helper works**

```bash
cd D:/Projects/Personal/usb-floppy-pi
python .pi-dev-helper.py run "uname -r; uname -m"
```

Expected output (or close):
```
6.12.75+rpt-rpi-v8
aarch64
```

- [ ] **Step 2: Install kernel headers + build tools on the Pi**

```bash
python .pi-dev-helper.py sudo "apt update && apt install -y dkms raspberrypi-kernel-headers build-essential bc bison flex libssl-dev"
```

Expected: package install completes without errors.

- [ ] **Step 3: Verify kernel build directory is usable**

```bash
python .pi-dev-helper.py run 'KDIR=/lib/modules/$(uname -r)/build && ls $KDIR/Makefile $KDIR/scripts/Kbuild $KDIR/include/linux/module.h && echo BUILD_OK'
```

Expected: lists three files + `BUILD_OK`.

- [ ] **Step 4: Build a hello-world kernel module to validate the toolchain**

```bash
python .pi-dev-helper.py run 'mkdir -p /tmp/khello && cat > /tmp/khello/hello.c << EOF
#include <linux/module.h>
#include <linux/init.h>
MODULE_LICENSE("GPL");
static int __init hello_init(void) { pr_info("khello: loaded\n"); return 0; }
static void __exit hello_exit(void) { pr_info("khello: unloaded\n"); }
module_init(hello_init);
module_exit(hello_exit);
EOF
cat > /tmp/khello/Makefile << EOF
obj-m += hello.o
all:
	\$(MAKE) -C /lib/modules/\$(shell uname -r)/build M=\$(PWD) modules
clean:
	\$(MAKE) -C /lib/modules/\$(shell uname -r)/build M=\$(PWD) clean
EOF
cd /tmp/khello && make 2>&1 | tail -5'
```

Expected: ends with something like `LD [M]  /tmp/khello/hello.ko` and no errors.

- [ ] **Step 5: Load and unload the module, check dmesg**

```bash
python .pi-dev-helper.py sudo "insmod /tmp/khello/hello.ko && dmesg -T | tail -3 && rmmod hello && dmesg -T | tail -2"
```

Expected: dmesg shows `khello: loaded` and then `khello: unloaded`.

- [ ] **Step 6: Cleanup test files**

```bash
python .pi-dev-helper.py run "rm -rf /tmp/khello"
```

- [ ] **Step 7: Commit anything needed**

No code change in this task. If `.pi-dev-helper.py` was new, it was already committed in the brainstorming phase. Verify clean status:

```bash
cd D:/Projects/Personal/usb-floppy-pi && git status --short
```

Expected: clean (only `.claude/` untracked which is harness state).

---

## Task 2: Fetch upstream kernel source files

We need to fork five files from Linux kernel v6.12. We download once and check them in to the repo.

**Files:**
- Create: `kernel/scripts/fetch-upstream.sh`
- Create: `kernel/UPSTREAM-DIFF.md`
- Create: `kernel/f_floppy.c` (copy of `drivers/usb/gadget/function/f_mass_storage.c`)
- Create: `kernel/f_floppy.h` (copy of `drivers/usb/gadget/function/f_mass_storage.h`)
- Create: `kernel/storage_common.c` (copy of `drivers/usb/gadget/function/storage_common.c`)
- Create: `kernel/storage_common.h` (copy of `drivers/usb/gadget/function/storage_common.h`)
- Create: `kernel/g_floppy_main.c` (copy of `drivers/usb/gadget/legacy/mass_storage.c`)

- [ ] **Step 1: Create the fetch script**

Create directory and file:

```bash
mkdir -p kernel/scripts
```

Write `kernel/scripts/fetch-upstream.sh`:

```bash
#!/usr/bin/env bash
# One-shot: fetch source files from Linux kernel v6.12 and place them as our forks.
# Run from repo root: kernel/scripts/fetch-upstream.sh
set -euo pipefail

KVERSION="6.12.75"
KMINOR="6.x"
TARBALL="linux-${KVERSION}.tar.xz"
URL="https://cdn.kernel.org/pub/linux/kernel/v${KMINOR}/${TARBALL}"

WORK=$(mktemp -d)
trap "rm -rf $WORK" EXIT

echo "==> Downloading $URL ..."
curl -L -o "$WORK/$TARBALL" "$URL"

echo "==> Extracting only the files we need ..."
tar -xJf "$WORK/$TARBALL" -C "$WORK" \
    "linux-${KVERSION}/drivers/usb/gadget/function/f_mass_storage.c" \
    "linux-${KVERSION}/drivers/usb/gadget/function/f_mass_storage.h" \
    "linux-${KVERSION}/drivers/usb/gadget/function/storage_common.c" \
    "linux-${KVERSION}/drivers/usb/gadget/function/storage_common.h" \
    "linux-${KVERSION}/drivers/usb/gadget/legacy/mass_storage.c"

cd kernel
echo "==> Copying as forks ..."
cp "$WORK/linux-${KVERSION}/drivers/usb/gadget/function/f_mass_storage.c"  ./f_floppy.c
cp "$WORK/linux-${KVERSION}/drivers/usb/gadget/function/f_mass_storage.h"  ./f_floppy.h
cp "$WORK/linux-${KVERSION}/drivers/usb/gadget/function/storage_common.c"  ./storage_common.c
cp "$WORK/linux-${KVERSION}/drivers/usb/gadget/function/storage_common.h"  ./storage_common.h
cp "$WORK/linux-${KVERSION}/drivers/usb/gadget/legacy/mass_storage.c"      ./g_floppy_main.c

echo "==> Done. Source files placed in kernel/."
echo "    Verify and commit. Source upstream: linux-${KVERSION}"
```

Make executable:

```bash
chmod +x kernel/scripts/fetch-upstream.sh
```

- [ ] **Step 2: Run the fetch (on the Pi, since Windows curl may have cert issues)**

```bash
python .pi-dev-helper.py run "mkdir -p /tmp/fetchwork && cd /tmp/fetchwork && rm -rf linux* && curl -sL -o linux-6.12.75.tar.xz https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-6.12.75.tar.xz && ls -la linux-6.12.75.tar.xz"
```

Expected: file is ~140 MB.

```bash
python .pi-dev-helper.py run "cd /tmp/fetchwork && tar -xJf linux-6.12.75.tar.xz linux-6.12.75/drivers/usb/gadget/function/f_mass_storage.c linux-6.12.75/drivers/usb/gadget/function/f_mass_storage.h linux-6.12.75/drivers/usb/gadget/function/storage_common.c linux-6.12.75/drivers/usb/gadget/function/storage_common.h linux-6.12.75/drivers/usb/gadget/legacy/mass_storage.c && ls -la linux-6.12.75/drivers/usb/gadget/function/ linux-6.12.75/drivers/usb/gadget/legacy/mass_storage.c"
```

Expected: 4 files in function/ and one in legacy/.

- [ ] **Step 3: Pull the files from Pi to our repo**

```bash
python .pi-dev-helper.py get /tmp/fetchwork/linux-6.12.75/drivers/usb/gadget/function/f_mass_storage.c kernel/f_floppy.c
python .pi-dev-helper.py get /tmp/fetchwork/linux-6.12.75/drivers/usb/gadget/function/f_mass_storage.h kernel/f_floppy.h
python .pi-dev-helper.py get /tmp/fetchwork/linux-6.12.75/drivers/usb/gadget/function/storage_common.c kernel/storage_common.c
python .pi-dev-helper.py get /tmp/fetchwork/linux-6.12.75/drivers/usb/gadget/function/storage_common.h kernel/storage_common.h
python .pi-dev-helper.py get /tmp/fetchwork/linux-6.12.75/drivers/usb/gadget/legacy/mass_storage.c kernel/g_floppy_main.c
```

Verify:

```bash
ls -la kernel/*.c kernel/*.h
wc -l kernel/*.c kernel/*.h
```

Expected: 4 .c files, 2 .h files (f_floppy.h + storage_common.h). The .c files combined are ~3500 lines.

- [ ] **Step 4: Cleanup Pi temp**

```bash
python .pi-dev-helper.py run "rm -rf /tmp/fetchwork"
```

- [ ] **Step 5: Write `kernel/UPSTREAM-DIFF.md` documenting the fork**

```markdown
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

- 2026-05-07: Initial fork, no modifications yet.
```

- [ ] **Step 6: Commit**

```bash
cd D:/Projects/Personal/usb-floppy-pi
git add kernel/scripts/fetch-upstream.sh kernel/UPSTREAM-DIFF.md kernel/*.c kernel/*.h
git commit -m "chore(kernel): fork f_mass_storage + storage_common + mass_storage from Linux v6.12.75"
```

---

## Task 3: Rename and adjust forked files for our naming

We renamed `f_mass_storage.{c,h}` → `f_floppy.{c,h}` and `mass_storage.c` → `g_floppy_main.c`. Their internals still reference each other by the old names. This task updates the internals to be self-consistent.

**Files:**
- Modify: `kernel/f_floppy.c` — change `#include "f_mass_storage.h"` → `#include "f_floppy.h"`
- Modify: `kernel/g_floppy_main.c` — change `#include "f_mass_storage.h"` → `#include "f_floppy.h"`, update module name strings
- Modify: `kernel/UPSTREAM-DIFF.md` — log the rename

- [ ] **Step 1: Update includes in `f_floppy.c`**

```bash
cd D:/Projects/Personal/usb-floppy-pi
grep -n 'f_mass_storage.h' kernel/f_floppy.c
```

Expected: shows the `#include "f_mass_storage.h"` line(s).

Edit `kernel/f_floppy.c`: replace `#include "f_mass_storage.h"` with `#include "f_floppy.h"`. Use sed:

```bash
sed -i 's/#include "f_mass_storage.h"/#include "f_floppy.h"/g' kernel/f_floppy.c
grep -n 'f_floppy.h\|f_mass_storage.h' kernel/f_floppy.c
```

Expected: lines now show `f_floppy.h` and no `f_mass_storage.h`.

- [ ] **Step 2: Update includes in `g_floppy_main.c`**

```bash
sed -i 's/#include "f_mass_storage.h"/#include "f_floppy.h"/g' kernel/g_floppy_main.c
grep -n 'f_mass_storage.h' kernel/g_floppy_main.c
```

Expected: no matches.

- [ ] **Step 3: Update MODULE_DESCRIPTION/AUTHOR strings in `g_floppy_main.c`**

The legacy module declares its identity. We need to rename it. Find the relevant lines:

```bash
grep -n 'MODULE_DESCRIPTION\|MODULE_AUTHOR\|MODULE_LICENSE' kernel/g_floppy_main.c
```

Open `kernel/g_floppy_main.c` and find the lines near the bottom (roughly line 200-260):

Original (typical):
```c
MODULE_DESCRIPTION(DRIVER_DESC);
MODULE_AUTHOR("Michal Nazarewicz");
MODULE_LICENSE("GPL");
```

Find `DRIVER_DESC` near top of file, change:
```c
#define DRIVER_DESC  "Mass Storage Gadget"
#define DRIVER_NAME  "g_mass_storage"
```
to:
```c
#define DRIVER_DESC  "USB Floppy Pi Gadget"
#define DRIVER_NAME  "g_floppy"
```

Also find any `pr_info` / `pr_warn` / `pr_err` calls that include the string `"g_mass_storage"` or `"Mass Storage"` and update similarly. For correctness do a search:

```bash
grep -n 'g_mass_storage\|"Mass Storage' kernel/g_floppy_main.c
```

Replace as needed. Most kernel modules only have a few of these.

- [ ] **Step 4: Update the `MODULE_ALIAS` if present**

```bash
grep -n 'MODULE_ALIAS' kernel/g_floppy_main.c
```

If there's a `MODULE_ALIAS("g_mass_storage")` or similar, leave it OUT (we don't want to alias our module; users explicitly use g_floppy).

- [ ] **Step 5: Append a line to UPSTREAM-DIFF.md**

```bash
cat >> kernel/UPSTREAM-DIFF.md <<'EOF'
- 2026-05-07: Renamed includes and MODULE_DESCRIPTION/DRIVER_NAME in g_floppy_main.c
  and f_floppy.c so the forked files refer to each other by their new names.
EOF
```

- [ ] **Step 6: Commit**

```bash
git add kernel/f_floppy.c kernel/g_floppy_main.c kernel/UPSTREAM-DIFF.md
git commit -m "refactor(kernel): rename forked references (f_mass_storage → f_floppy, g_mass_storage → g_floppy)"
```

---

## Task 4: Write Kbuild Makefile + first build

Now we have correctly-renamed files but no Makefile. Add Kbuild and verify the module compiles.

**Files:**
- Create: `kernel/Makefile`

- [ ] **Step 1: Write `kernel/Makefile`**

```makefile
# Out-of-tree Kbuild Makefile for g_floppy.ko
# Built by `make` (uses host kernel) or DKMS (uses target kernel).

obj-m += g_floppy.o
g_floppy-y := g_floppy_main.o f_floppy.o storage_common.o

# Note: floppy_throttle.o and floppy_buzzer.o get added in later tasks.

KDIR ?= /lib/modules/$(shell uname -r)/build

all:
	$(MAKE) -C $(KDIR) M=$(PWD) modules

clean:
	$(MAKE) -C $(KDIR) M=$(PWD) clean

modules_install:
	$(MAKE) -C $(KDIR) M=$(PWD) modules_install

.PHONY: all clean modules_install
```

- [ ] **Step 2: Sync the kernel/ dir to the Pi**

```bash
cd D:/Projects/Personal/usb-floppy-pi
python .pi-dev-helper.py run "mkdir -p /home/pi/kernel-dev"
# Use sftp via paramiko to sync. Quickest: tar+ssh on dev box, untar on Pi.
tar -cf - -C . kernel | python .pi-dev-helper.py run "tar -xf - -C /home/pi/kernel-dev"
python .pi-dev-helper.py run "ls /home/pi/kernel-dev/kernel/"
```

Expected: lists the .c, .h, Makefile, etc.

> **Note:** the helper doesn't have a "tar pipe" mode so the above won't work as-is. Instead, use rsync via the helper's get/put for each file individually, or extend the helper. Simplest workaround for this task:

```bash
# Use scp directly via Windows OpenSSH (will prompt for password each time, OR
# use sshpass replacement: a Python one-liner that runs scp via paramiko's SFTP)
python -c "
import paramiko, os, glob
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect('floppyusb', username='pi', password='floppy', look_for_keys=False, allow_agent=False)
sftp = c.open_sftp()
try: sftp.mkdir('/home/pi/kernel-dev')
except: pass
try: sftp.mkdir('/home/pi/kernel-dev/kernel')
except: pass
try: sftp.mkdir('/home/pi/kernel-dev/kernel/scripts')
except: pass
for f in glob.glob('kernel/*.c') + glob.glob('kernel/*.h') + ['kernel/Makefile', 'kernel/UPSTREAM-DIFF.md']:
    sftp.put(f, '/home/pi/' + f.replace(chr(92), '/'))
    print('uploaded', f)
sftp.close(); c.close()
"
```

- [ ] **Step 3: Try to build on the Pi**

```bash
python .pi-dev-helper.py run "cd /home/pi/kernel-dev/kernel && make 2>&1 | tail -30"
```

Expected outcomes:
- **Success case:** ends with `LD [M] /home/pi/kernel-dev/kernel/g_floppy.ko` and no errors. Skip to Step 5.
- **Failure case:** errors/warnings about missing symbols, undefined references, etc. The most likely failure modes:
  - `f_mass_storage.h` references something not in the `kernel/` directory → see step 4
  - The kernel `legacy/mass_storage.c` source has a `module_init`/`module_exit` that conflicts with `f_mass_storage.c`'s gadget framework hooks if both are statically linked into one .ko — needs adjustment
  - `usb_function_register` / `usb_composite_probe` mismatches between forked code

- [ ] **Step 4 (only if build failed): Debug build errors iteratively**

Read the build output, identify each unresolved symbol or missing header. Common fixes:
- Missing prototype: search the original kernel for that symbol's header, add `#include` to the relevant fork file
- `multiple definition`: between f_floppy.c and g_floppy_main.c, the legacy gadget already statically links f_mass_storage internally. Our `g_floppy-y := g_floppy_main.o f_floppy.o storage_common.o` is wrong — the legacy mass_storage.c already does `#include "f_mass_storage.h"` and uses the function via the composite framework. Investigate: maybe we just need `g_floppy-y := g_floppy_main.o storage_common.o` and let g_floppy_main.c pull in f_floppy via the composite registration.

Apply fixes, re-run the upload step (step 2's Python one-liner) and rebuild. Iterate until clean build.

- [ ] **Step 5: Verify the .ko file exists**

```bash
python .pi-dev-helper.py run "ls -la /home/pi/kernel-dev/kernel/g_floppy.ko && modinfo /home/pi/kernel-dev/kernel/g_floppy.ko 2>&1 | head -15"
```

Expected: `g_floppy.ko` exists, `modinfo` shows its parameters and signed/unsigned status.

- [ ] **Step 6: Commit**

```bash
cd D:/Projects/Personal/usb-floppy-pi
git add kernel/Makefile
# Also commit any debugging fixes from Step 4
git add kernel/
git commit -m "feat(kernel): Kbuild Makefile, first successful build of g_floppy.ko"
```

---

## Task 5: Load the module + verify USB enumeration (no behavior change yet)

We have a buildable module that's a copy of g_mass_storage. Confirm it works as a drop-in replacement: stop the Phase 1 service, load g_floppy with module params, validate USB enumeration on the host.

**Files:** none (Pi-side runtime)

- [ ] **Step 1: Stop Phase 1 service and tear down current gadget**

```bash
python .pi-dev-helper.py sudo "systemctl stop usb-floppy-pi"
python .pi-dev-helper.py sudo "rmmod g_mass_storage 2>/dev/null; rmmod libcomposite 2>/dev/null; true"
python .pi-dev-helper.py sudo 'echo "" > /sys/kernel/config/usb_gadget/floppy/UDC 2>/dev/null; rm -rf /sys/kernel/config/usb_gadget/floppy 2>/dev/null; true'
python .pi-dev-helper.py run "ls /sys/class/udc/"
```

Expected: UDC list shows e.g. `3f980000.usb`, no gadget bound to it.

- [ ] **Step 2: Create a test backing file**

```bash
python .pi-dev-helper.py run "ls -la /home/pi/floppies/"
# pick any existing .img, or create a 1.44MB test image:
python .pi-dev-helper.py run "test -f /home/pi/floppies/Test/test.img || (mkdir -p /home/pi/floppies/Test && dd if=/dev/zero of=/home/pi/floppies/Test/test.img bs=1024 count=1440)"
```

- [ ] **Step 3: Load g_floppy with the test file**

```bash
python .pi-dev-helper.py sudo "insmod /home/pi/kernel-dev/kernel/g_floppy.ko file=/home/pi/floppies/Test/test.img stall=0 removable=1"
python .pi-dev-helper.py run "lsmod | grep -E 'g_floppy|libcomposite|udc_core'"
python .pi-dev-helper.py run "dmesg -T | tail -15"
```

Expected: `g_floppy` listed in lsmod. dmesg shows messages about the gadget binding to a UDC.

- [ ] **Step 4: Verify the gadget is bound**

```bash
python .pi-dev-helper.py run "ls /sys/class/udc/ && cat /sys/class/udc/*/state 2>&1"
```

Expected: state shows e.g. `configured` or `addressed` (means a host is talking to it).

- [ ] **Step 5: Connect the Pi to the host PC, verify enumeration**

(Manual step — physical action required)

Connect the data USB cable from the Pi to a host PC. On the host:
- **Linux/macOS:** `lsusb` should show e.g. `Linux Foundation File-Stor Gadget` (since we forked from g_mass_storage with default IDs). Run `dmesg | tail` to see the storage device name.
- **Windows:** Device Manager shows new "USB Mass Storage Device". File Explorer shows new drive letter with the contents of test.img (likely "drive needs to be formatted" since it's empty).

If the host sees the device, enumeration works. We have the equivalent of Phase 1 running through our forked module.

- [ ] **Step 6: Unload, restore Phase 1**

```bash
python .pi-dev-helper.py sudo "rmmod g_floppy"
python .pi-dev-helper.py sudo "systemctl start usb-floppy-pi"
python .pi-dev-helper.py run "sleep 2 && systemctl is-active usb-floppy-pi"
```

Expected: `active`. Phase 1 is back.

- [ ] **Step 7: Document outcome — append to UPSTREAM-DIFF.md and commit**

```bash
cat >> kernel/UPSTREAM-DIFF.md <<'EOF'
- 2026-05-07: Module loads and enumerates USB device successfully on host.
  Verified equivalent to upstream g_mass_storage behavior. No further changes
  in this task (bootstrap only).
EOF
git add kernel/UPSTREAM-DIFF.md
git commit -m "test(kernel): verified g_floppy.ko loads and enumerates USB on host (Phase 2.1 done)"
```

---

## Task 6: Change interface SubClass to UFI

Modify `f_floppy.c` to declare the gadget interface as USB-FDD (UFI) instead of SCSI.

**Files:**
- Modify: `kernel/f_floppy.c`
- Modify: `kernel/UPSTREAM-DIFF.md`

- [ ] **Step 1: Locate the interface descriptor**

```bash
grep -n 'bInterfaceSubClass\|USB_SC_SCSI' kernel/f_floppy.c
```

Expected: shows a struct around line ~150 like:
```c
static struct usb_interface_descriptor fsg_intf_desc = {
    ...
    .bInterfaceClass = USB_CLASS_MASS_STORAGE,
    .bInterfaceSubClass = USB_SC_SCSI,
    .bInterfaceProtocol = USB_PR_BULK,
    ...
};
```

- [ ] **Step 2: Add a module param for subclass selection (with UFI as default)**

Edit `kernel/f_floppy.c`. Near the top of the file (after the `#include`s), add:

```c
/* === usb-floppy-pi additions: configurable subclass === */
#include <linux/moduleparam.h>

static char *floppy_subclass = "ufi";
module_param_named(subclass, floppy_subclass, charp, 0444);
MODULE_PARM_DESC(subclass, "USB interface subclass: ufi (0x04, default) or scsi (0x06)");

/* Resolved at module init from the param string. */
static u8 floppy_subclass_value = USB_SC_UFI;

static int __maybe_unused floppy_resolve_subclass(void)
{
    if (!strcmp(floppy_subclass, "ufi")) {
        floppy_subclass_value = USB_SC_UFI;
        return 0;
    }
    if (!strcmp(floppy_subclass, "scsi")) {
        floppy_subclass_value = USB_SC_SCSI;
        return 0;
    }
    pr_err("g_floppy: unknown subclass '%s'; valid: ufi, scsi\n", floppy_subclass);
    return -EINVAL;
}
/* === end usb-floppy-pi additions === */
```

- [ ] **Step 3: Change the descriptor to use the resolved value at bind time**

Find the `fsg_intf_desc` struct. Change the static field:

```c
.bInterfaceSubClass = USB_SC_SCSI,   // OLD
```

to:

```c
.bInterfaceSubClass = USB_SC_UFI,    // default; overridden at bind from module param
```

Then find the `fsg_bind` function (or `fsg_common_bind` or similar — search):

```bash
grep -n 'fsg_bind\|fsg_common_bind' kernel/f_floppy.c
```

Inside the bind function, before the descriptor is registered with the host, add:

```c
floppy_resolve_subclass();
fsg_intf_desc.bInterfaceSubClass = floppy_subclass_value;
```

This applies the module param's choice. If the user passed `subclass=scsi`, we revert to SCSI; otherwise UFI.

- [ ] **Step 4: Rebuild and re-upload to Pi**

```bash
# Re-upload via the SFTP one-liner from Task 4 step 2
python -c "
import paramiko
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect('floppyusb', username='pi', password='floppy', look_for_keys=False, allow_agent=False)
sftp = c.open_sftp()
sftp.put('kernel/f_floppy.c', '/home/pi/kernel-dev/kernel/f_floppy.c')
sftp.close(); c.close()
"

python .pi-dev-helper.py run "cd /home/pi/kernel-dev/kernel && make 2>&1 | tail -5"
```

Expected: clean build, no errors or new warnings.

- [ ] **Step 5: Stop Phase 1, load module with default (UFI), verify subclass on host**

```bash
python .pi-dev-helper.py sudo "systemctl stop usb-floppy-pi && rmmod g_mass_storage 2>/dev/null; true"
python .pi-dev-helper.py sudo "insmod /home/pi/kernel-dev/kernel/g_floppy.ko file=/home/pi/floppies/Test/test.img removable=1"
python .pi-dev-helper.py run "dmesg -T | tail -5"
```

Connect the host PC. On Linux host: `lsusb -v -d 0525:a4a5 2>/dev/null | grep -E 'bInterfaceClass|bInterfaceSubClass|bInterfaceProtocol'` should show:
```
bInterfaceClass         8 Mass Storage
bInterfaceSubClass      4 USB Floppy
bInterfaceProtocol      80 Bulk-Only
```

(SubClass 4 = UFI). 

On Windows host, Device Manager → Properties → Details → Hardware IDs should show `USB\COMPAT_VID_xxxx&Class_08&SubClass_04&Prot_50` — note SubClass 04 (UFI).

- [ ] **Step 6: Verify Windows still recognizes as floppy when no media**

Eject by emptying the file param (we'll do this via sysfs later; for now use the legacy module's `lun0/file` path):

```bash
python .pi-dev-helper.py run "ls /sys/class/udc/*/device/gadget*/lun*/file 2>/dev/null; ls /sys/devices/platform/soc/*/gadget*/lun*/file 2>/dev/null"
```

Find the actual path on this kernel and write a newline to it:

```bash
python .pi-dev-helper.py sudo 'echo "" > $(find /sys -name "file" -path "*/lun.*/file" -o -path "*/lun*/file" 2>/dev/null | head -1)'
```

On Windows, refresh Device Manager. **Now the device should still appear as "Floppy disk drive"** (regardless of media presence). This is the win Phase 2.2 brings.

- [ ] **Step 7: Cleanup, append to UPSTREAM-DIFF, commit**

```bash
python .pi-dev-helper.py sudo "rmmod g_floppy; systemctl start usb-floppy-pi"

cat >> kernel/UPSTREAM-DIFF.md <<'EOF'
- 2026-05-07: f_floppy.c modified to declare bInterfaceSubClass=UFI by default;
  added module param 'subclass' (ufi|scsi) for runtime override at load time.
EOF

git add kernel/f_floppy.c kernel/UPSTREAM-DIFF.md
git commit -m "feat(kernel): declare USB interface subclass UFI for true floppy identity"
```

---

## Task 7: Add sysfs class registration with file/ro/inquiry attributes

We want a clean userspace interface at `/sys/class/usb_floppy/usb-floppy-pi/`. Add the registration to `g_floppy_main.c` plus initial attributes that mirror what configfs offered.

**Files:**
- Modify: `kernel/g_floppy_main.c`
- Modify: `kernel/UPSTREAM-DIFF.md`

- [ ] **Step 1: Add sysfs class registration to `g_floppy_main.c`**

Near the top of `g_floppy_main.c`, add:

```c
#include <linux/device.h>
#include <linux/sysfs.h>
```

Near where the module exit / cleanup happens, add the class machinery. Insert this section before `module_init`:

```c
/* === usb-floppy-pi: sysfs class === */

static struct class *usb_floppy_class;
static struct device *usb_floppy_dev;

/* Forward references. The actual fsg state is reachable via gfs_dev (or
 * equivalent in the legacy module) — we'll wire these to the real values
 * in subsequent tasks. For now, return placeholder values so the registration
 * works without crashing. */

static ssize_t lun0_file_show(struct device *d, struct device_attribute *a, char *buf)
{
    /* TODO Task 8: read the actual current backing file path from fsg state */
    return scnprintf(buf, PAGE_SIZE, "(placeholder)\n");
}

static ssize_t lun0_file_store(struct device *d, struct device_attribute *a,
                                const char *buf, size_t count)
{
    /* TODO Task 8: write to the real LUN backing file */
    pr_info("g_floppy: lun0/file = %.*s (placeholder, not wired yet)\n",
            (int)count, buf);
    return count;
}
static DEVICE_ATTR_RW(lun0_file);

static struct attribute *usb_floppy_attrs[] = {
    &dev_attr_lun0_file.attr,
    NULL,
};

static const struct attribute_group usb_floppy_group = {
    .attrs = usb_floppy_attrs,
};
static const struct attribute_group *usb_floppy_groups[] = {
    &usb_floppy_group,
    NULL,
};

static int usb_floppy_sysfs_init(void)
{
    int err;

    usb_floppy_class = class_create("usb_floppy");
    if (IS_ERR(usb_floppy_class))
        return PTR_ERR(usb_floppy_class);

    usb_floppy_dev = device_create_with_groups(usb_floppy_class, NULL,
                                                MKDEV(0, 0), NULL,
                                                usb_floppy_groups,
                                                "usb-floppy-pi");
    if (IS_ERR(usb_floppy_dev)) {
        err = PTR_ERR(usb_floppy_dev);
        class_destroy(usb_floppy_class);
        return err;
    }
    pr_info("g_floppy: /sys/class/usb_floppy/usb-floppy-pi registered\n");
    return 0;
}

static void usb_floppy_sysfs_exit(void)
{
    if (usb_floppy_dev)
        device_destroy(usb_floppy_class, MKDEV(0, 0));
    if (usb_floppy_class)
        class_destroy(usb_floppy_class);
}
/* === end usb-floppy-pi sysfs class === */
```

> **Note on `class_create`:** the signature changed in kernel 6.4. For older kernels (pre-6.4), use `class_create(THIS_MODULE, "usb_floppy")`. Our target is 6.12, so the single-arg form is correct.

- [ ] **Step 2: Wire init/exit into the module's lifecycle**

Find the module's existing `module_init` and `module_exit` callbacks (typically at the bottom of `g_floppy_main.c`).

Locate the `init` function (e.g. `msg_init` or `gfs_init`) and add at the end before the final `return`:

```c
{
    int sysfs_err = usb_floppy_sysfs_init();
    if (sysfs_err)
        pr_warn("g_floppy: failed to register sysfs class (%d), continuing without\n",
                sysfs_err);
}
```

Find the `exit` function (e.g. `msg_cleanup`) and add at the start:

```c
usb_floppy_sysfs_exit();
```

- [ ] **Step 3: Build, upload, reload**

```bash
python -c "
import paramiko
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect('floppyusb', username='pi', password='floppy', look_for_keys=False, allow_agent=False)
sftp = c.open_sftp()
sftp.put('kernel/g_floppy_main.c', '/home/pi/kernel-dev/kernel/g_floppy_main.c')
sftp.close(); c.close()
"
python .pi-dev-helper.py run "cd /home/pi/kernel-dev/kernel && make 2>&1 | tail -5"
```

Expected: clean build.

```bash
python .pi-dev-helper.py sudo "rmmod g_floppy 2>/dev/null; insmod /home/pi/kernel-dev/kernel/g_floppy.ko file=/home/pi/floppies/Test/test.img removable=1"
```

- [ ] **Step 4: Verify sysfs entries exist**

```bash
python .pi-dev-helper.py run "ls -la /sys/class/usb_floppy/usb-floppy-pi/"
python .pi-dev-helper.py run "cat /sys/class/usb_floppy/usb-floppy-pi/lun0_file"
```

Expected: directory exists, `lun0_file` reads `(placeholder)`.

```bash
python .pi-dev-helper.py sudo "echo /tmp/something.img > /sys/class/usb_floppy/usb-floppy-pi/lun0_file"
python .pi-dev-helper.py run "dmesg -T | tail -3"
```

Expected: dmesg shows `g_floppy: lun0/file = /tmp/something.img\n (placeholder, not wired yet)`.

- [ ] **Step 5: Unload, append diff, commit**

```bash
python .pi-dev-helper.py sudo "rmmod g_floppy"

cat >> kernel/UPSTREAM-DIFF.md <<'EOF'
- 2026-05-07: g_floppy_main.c gained sysfs class registration at
  /sys/class/usb_floppy/usb-floppy-pi/ with placeholder lun0_file attribute.
  The store callback logs to dmesg but does not yet plumb to the FSG core.
EOF

git add kernel/g_floppy_main.c kernel/UPSTREAM-DIFF.md
git commit -m "feat(kernel): register /sys/class/usb_floppy class with placeholder lun0_file attr"
```

---

## Task 8: Wire sysfs `lun0_file` to the real FSG backing file

The placeholder from Task 7 needs to actually swap the LUN's backing file. We need to access the FSG common state from the sysfs callback.

**Files:**
- Modify: `kernel/g_floppy_main.c`
- Modify: `kernel/UPSTREAM-DIFF.md`

- [ ] **Step 1: Find the FSG state pointer in `g_floppy_main.c`**

Look for the static variables that hold FSG state. Search for `fsg_common`, `fsg_lun_opts`, `fsg_dev`, `gfs_dev`, or `module_data`:

```bash
grep -n 'fsg_common\|fsg_dev\|module_data\|module_param.*file' kernel/g_floppy_main.c | head -20
```

The legacy mass_storage module typically has a `static struct fsg_module_parameters mod_data` and uses helpers from f_mass_storage.h to access the LUN array. Find the array of LUNs.

- [ ] **Step 2: Use the FSG helper to set the file**

The fsg_common struct from `f_floppy.h` has a `luns[]` array. Each LUN has `file` (the open struct file). The kernel provides `fsg_lun_open()` and `fsg_lun_close()` helpers in `storage_common.c`.

Add at the top of `g_floppy_main.c` (after the existing includes):

```c
extern struct fsg_common *fsg_common_get_g_floppy(void);
/* This is implemented at the end of f_floppy.c — see Task 8 step 3 */
```

- [ ] **Step 3: Expose `fsg_common` from `f_floppy.c`**

In `kernel/f_floppy.c`, find where the `fsg_common` is allocated (in `fsg_common_alloc` or similar). After it's stored in the global state, add a getter function. At the end of the file, add:

```c
/* === usb-floppy-pi: expose the FSG common state for sysfs callbacks === */
static struct fsg_common *g_floppy_common_ref;

void g_floppy_set_common_ref(struct fsg_common *c) { g_floppy_common_ref = c; }
EXPORT_SYMBOL_GPL(g_floppy_set_common_ref);

struct fsg_common *fsg_common_get_g_floppy(void) { return g_floppy_common_ref; }
EXPORT_SYMBOL_GPL(fsg_common_get_g_floppy);
/* === end usb-floppy-pi additions === */
```

In the same file, find the `fsg_common_setup` (or `fsg_common_alloc`) function and after it succeeds, call:

```c
g_floppy_set_common_ref(common);
```

(`common` is the pointer to the freshly-allocated fsg_common in scope at that point.)

- [ ] **Step 4: Update `lun0_file_show` and `lun0_file_store` in `g_floppy_main.c`**

Replace the placeholder implementations:

```c
static ssize_t lun0_file_show(struct device *d, struct device_attribute *a, char *buf)
{
    struct fsg_common *c = fsg_common_get_g_floppy();
    if (!c || !c->nluns)
        return scnprintf(buf, PAGE_SIZE, "\n");
    return fsg_show_file(c->luns[0], &c->filesem, buf);
}

static ssize_t lun0_file_store(struct device *d, struct device_attribute *a,
                                const char *buf, size_t count)
{
    struct fsg_common *c = fsg_common_get_g_floppy();
    if (!c || !c->nluns)
        return -ENODEV;
    return fsg_store_file(c->luns[0], &c->filesem, buf, count);
}
```

The functions `fsg_show_file` and `fsg_store_file` are already exported by `storage_common.c`. They handle the lun's filesem and SCSI sense data correctly (including the empty-string eject case).

- [ ] **Step 5: Add `lun0_ro` and `lun0_inquiry_string` similarly**

After the lun0_file definition, add:

```c
static ssize_t lun0_ro_show(struct device *d, struct device_attribute *a, char *buf)
{
    struct fsg_common *c = fsg_common_get_g_floppy();
    if (!c || !c->nluns)
        return scnprintf(buf, PAGE_SIZE, "0\n");
    return fsg_show_ro(c->luns[0], buf);
}
static ssize_t lun0_ro_store(struct device *d, struct device_attribute *a,
                              const char *buf, size_t count)
{
    struct fsg_common *c = fsg_common_get_g_floppy();
    if (!c || !c->nluns)
        return -ENODEV;
    return fsg_store_ro(c->luns[0], &c->filesem, buf, count);
}
static DEVICE_ATTR_RW(lun0_ro);

static ssize_t lun0_inquiry_string_show(struct device *d, struct device_attribute *a,
                                         char *buf)
{
    struct fsg_common *c = fsg_common_get_g_floppy();
    if (!c || !c->nluns)
        return scnprintf(buf, PAGE_SIZE, "\n");
    return fsg_show_inquiry_string(c->luns[0], buf);
}
static ssize_t lun0_inquiry_string_store(struct device *d, struct device_attribute *a,
                                          const char *buf, size_t count)
{
    struct fsg_common *c = fsg_common_get_g_floppy();
    if (!c || !c->nluns)
        return -ENODEV;
    return fsg_store_inquiry_string(c->luns[0], buf, count);
}
static DEVICE_ATTR_RW(lun0_inquiry_string);
```

Update the `usb_floppy_attrs[]` array:

```c
static struct attribute *usb_floppy_attrs[] = {
    &dev_attr_lun0_file.attr,
    &dev_attr_lun0_ro.attr,
    &dev_attr_lun0_inquiry_string.attr,
    NULL,
};
```

- [ ] **Step 6: Build, upload, reload, test full flow**

```bash
# Upload both files
python -c "
import paramiko
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect('floppyusb', username='pi', password='floppy', look_for_keys=False, allow_agent=False)
sftp = c.open_sftp()
sftp.put('kernel/f_floppy.c', '/home/pi/kernel-dev/kernel/f_floppy.c')
sftp.put('kernel/g_floppy_main.c', '/home/pi/kernel-dev/kernel/g_floppy_main.c')
sftp.close(); c.close()
"
python .pi-dev-helper.py run "cd /home/pi/kernel-dev/kernel && make 2>&1 | tail -5"
python .pi-dev-helper.py sudo "rmmod g_floppy 2>/dev/null; insmod /home/pi/kernel-dev/kernel/g_floppy.ko file=/home/pi/floppies/Test/test.img removable=1"
python .pi-dev-helper.py run "ls /sys/class/usb_floppy/usb-floppy-pi/ && cat /sys/class/usb_floppy/usb-floppy-pi/lun0_file"
```

Expected: file shows `/home/pi/floppies/Test/test.img`.

```bash
python .pi-dev-helper.py sudo "echo > /sys/class/usb_floppy/usb-floppy-pi/lun0_file"
python .pi-dev-helper.py run "cat /sys/class/usb_floppy/usb-floppy-pi/lun0_file"
```

Expected: empty (eject succeeded).

```bash
python .pi-dev-helper.py sudo "echo /home/pi/floppies/Test/test.img > /sys/class/usb_floppy/usb-floppy-pi/lun0_file"
python .pi-dev-helper.py run "cat /sys/class/usb_floppy/usb-floppy-pi/lun0_file"
```

Expected: shows the path again. Mount succeeded.

- [ ] **Step 7: Append diff, commit**

```bash
python .pi-dev-helper.py sudo "rmmod g_floppy"

cat >> kernel/UPSTREAM-DIFF.md <<'EOF'
- 2026-05-07: f_floppy.c exposes fsg_common via g_floppy_set_common_ref() /
  fsg_common_get_g_floppy() so g_floppy_main.c sysfs callbacks can plumb to
  real LUN state. Sysfs gained lun0_file, lun0_ro, lun0_inquiry_string with
  full read/write semantics (empty-string write to lun0_file ejects).
EOF

git add kernel/f_floppy.c kernel/g_floppy_main.c kernel/UPSTREAM-DIFF.md
git commit -m "feat(kernel): wire /sys/class/usb_floppy lun0/{file,ro,inquiry_string} to real FSG state"
```

---

## Task 9: Implement `floppy_throttle` skeleton

Create the throttle module, hook it from f_floppy.c at I/O sites. Initially just logs LBA + nblocks per request to dmesg so we can verify hooks fire correctly.

**Files:**
- Create: `kernel/floppy_throttle.h`
- Create: `kernel/floppy_throttle.c`
- Modify: `kernel/Makefile` — add `floppy_throttle.o` to `g_floppy-y`
- Modify: `kernel/f_floppy.c` — add hooks
- Modify: `kernel/UPSTREAM-DIFF.md`

- [ ] **Step 1: Write `kernel/floppy_throttle.h`**

```c
#ifndef FLOPPY_THROTTLE_H
#define FLOPPY_THROTTLE_H

#include <linux/types.h>
#include <linux/spinlock.h>

struct floppy_throttle_state {
    u32 read_kbps;
    u32 write_kbps;
    u32 seek_us;
    u32 last_track;
    spinlock_t lock;
};

/* Lifecycle */
int  floppy_throttle_init(struct floppy_throttle_state *st);
void floppy_throttle_exit(struct floppy_throttle_state *st);

/* Hooks called from the FSG read/write paths */
void floppy_throttle_on_read(struct floppy_throttle_state *st,
                              u32 lba, u32 nblocks);
void floppy_throttle_on_write(struct floppy_throttle_state *st,
                               u32 lba, u32 nblocks);

/* Configuration via preset name */
int  floppy_throttle_set_preset(struct floppy_throttle_state *st,
                                 const char *name);
ssize_t floppy_throttle_show_preset(struct floppy_throttle_state *st,
                                     char *buf);

/* Direct reads of the resolved preset values (for sysfs read-only attrs) */
static inline u32 floppy_throttle_read_kbps(struct floppy_throttle_state *st)
    { return st->read_kbps; }
static inline u32 floppy_throttle_write_kbps(struct floppy_throttle_state *st)
    { return st->write_kbps; }
static inline u32 floppy_throttle_seek_us(struct floppy_throttle_state *st)
    { return st->seek_us; }

/* Module-level singleton accessor — allows sysfs callbacks in
 * g_floppy_main.c to reach the throttle state without passing it around. */
struct floppy_throttle_state *floppy_throttle_get(void);

#endif /* FLOPPY_THROTTLE_H */
```

- [ ] **Step 2: Write `kernel/floppy_throttle.c`**

```c
#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/delay.h>
#include <linux/string.h>
#include "floppy_throttle.h"

/* CHS for 1.44MB HD floppy: 80 cylinders * 2 heads * 18 sectors */
#define FLOPPY_SECTORS_PER_TRACK 36   /* 18 sectors * 2 heads, treating C/H as one "track" */

struct floppy_throttle_preset {
    const char *name;
    u32 read_kbps;
    u32 write_kbps;
    u32 seek_us;
};

static const struct floppy_throttle_preset PRESETS[] = {
    { "floppy-real",  50, 30, 6000 },
    { "floppy-fast", 200, 200, 500 },
    { "unthrottled",   0,   0,   0 },
};

static struct floppy_throttle_state *g_throttle;

int floppy_throttle_init(struct floppy_throttle_state *st)
{
    spin_lock_init(&st->lock);
    /* Default to floppy-real */
    st->read_kbps = PRESETS[0].read_kbps;
    st->write_kbps = PRESETS[0].write_kbps;
    st->seek_us = PRESETS[0].seek_us;
    st->last_track = ~0u;  /* sentinel: first I/O always counts as a seek */
    g_throttle = st;
    pr_info("g_floppy: throttle init, default preset=floppy-real "
            "(read=%u kbps, write=%u kbps, seek=%u us)\n",
            st->read_kbps, st->write_kbps, st->seek_us);
    return 0;
}

void floppy_throttle_exit(struct floppy_throttle_state *st)
{
    g_throttle = NULL;
}

struct floppy_throttle_state *floppy_throttle_get(void)
{
    return g_throttle;
}

int floppy_throttle_set_preset(struct floppy_throttle_state *st, const char *name)
{
    int i;
    char clean[32];
    /* Strip trailing newline that sysfs writes often include. */
    strncpy(clean, name, sizeof(clean) - 1);
    clean[sizeof(clean) - 1] = '\0';
    for (i = 0; clean[i]; i++)
        if (clean[i] == '\n') { clean[i] = '\0'; break; }

    for (i = 0; i < ARRAY_SIZE(PRESETS); i++) {
        if (!strcmp(clean, PRESETS[i].name)) {
            unsigned long flags;
            spin_lock_irqsave(&st->lock, flags);
            st->read_kbps = PRESETS[i].read_kbps;
            st->write_kbps = PRESETS[i].write_kbps;
            st->seek_us = PRESETS[i].seek_us;
            spin_unlock_irqrestore(&st->lock, flags);
            pr_info("g_floppy: throttle preset=%s\n", PRESETS[i].name);
            return 0;
        }
    }
    pr_warn("g_floppy: unknown throttle preset '%s'\n", clean);
    return -EINVAL;
}

ssize_t floppy_throttle_show_preset(struct floppy_throttle_state *st, char *buf)
{
    int i;
    for (i = 0; i < ARRAY_SIZE(PRESETS); i++) {
        if (st->read_kbps == PRESETS[i].read_kbps &&
            st->write_kbps == PRESETS[i].write_kbps &&
            st->seek_us == PRESETS[i].seek_us) {
            return scnprintf(buf, PAGE_SIZE, "%s\n", PRESETS[i].name);
        }
    }
    /* No preset matches — must be a custom config (not yet supported via this API) */
    return scnprintf(buf, PAGE_SIZE, "custom\n");
}

static void apply_io_delay(u32 lba, u32 nblocks, u32 kbps,
                            struct floppy_throttle_state *st)
{
    u32 track, io_us;

    if (kbps == 0)
        return;  /* unthrottled */

    track = lba / FLOPPY_SECTORS_PER_TRACK;
    if (track != st->last_track && st->seek_us > 0) {
        usleep_range(st->seek_us, st->seek_us + 500);
        st->last_track = track;
    }

    io_us = (nblocks * 512U * 1000U) / kbps;
    if (io_us > 0)
        usleep_range(io_us, io_us + (io_us / 10) + 1);
}

void floppy_throttle_on_read(struct floppy_throttle_state *st,
                              u32 lba, u32 nblocks)
{
    if (!st) return;
    apply_io_delay(lba, nblocks, st->read_kbps, st);
}

void floppy_throttle_on_write(struct floppy_throttle_state *st,
                               u32 lba, u32 nblocks)
{
    if (!st) return;
    apply_io_delay(lba, nblocks, st->write_kbps, st);
}
```

- [ ] **Step 3: Update `kernel/Makefile`**

Change:
```
g_floppy-y := g_floppy_main.o f_floppy.o storage_common.o
```
to:
```
g_floppy-y := g_floppy_main.o f_floppy.o storage_common.o floppy_throttle.o
```

- [ ] **Step 4: Hook the throttle from `f_floppy.c`**

In `kernel/f_floppy.c`, add at the top (after existing includes):

```c
#include "floppy_throttle.h"
```

Find the `do_read` function:

```bash
grep -n '^static int do_read' kernel/f_floppy.c
```

Inside `do_read`, the LBA is parsed near the start (it comes from the SCSI CDB). Look for the first place after the LBA is in scope, and add a call to `floppy_throttle_on_read`. Typical location:

```c
static int do_read(struct fsg_common *common)
{
    struct fsg_lun *curlun = common->curlun;
    u32 lba;
    ...
    lba = get_unaligned_be32(&common->cmnd[2]);  /* or similar */
    /* === usb-floppy-pi: throttle hook === */
    floppy_throttle_on_read(floppy_throttle_get(),
                             lba, common->data_size_from_cmnd / curlun->blksize);
    /* === end === */
    ...
}
```

(Adapt the exact field names to whatever's in scope at that point in `do_read`.) Repeat for `do_write`.

- [ ] **Step 5: Initialize the throttle in `g_floppy_main.c`**

In the module init (where you added `usb_floppy_sysfs_init()` in Task 7), also call:

```c
{
    static struct floppy_throttle_state throttle_state;
    floppy_throttle_init(&throttle_state);
}
```

In the module exit, before `usb_floppy_sysfs_exit()`:

```c
floppy_throttle_exit(floppy_throttle_get());
```

- [ ] **Step 6: Build, upload, reload, validate**

```bash
python -c "
import paramiko
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect('floppyusb', username='pi', password='floppy', look_for_keys=False, allow_agent=False)
sftp = c.open_sftp()
for f in ['Makefile', 'floppy_throttle.h', 'floppy_throttle.c', 'f_floppy.c', 'g_floppy_main.c']:
    sftp.put('kernel/' + f, '/home/pi/kernel-dev/kernel/' + f)
    print('uploaded', f)
sftp.close(); c.close()
"
python .pi-dev-helper.py run "cd /home/pi/kernel-dev/kernel && make 2>&1 | tail -5"
python .pi-dev-helper.py sudo "rmmod g_floppy 2>/dev/null; insmod /home/pi/kernel-dev/kernel/g_floppy.ko file=/home/pi/floppies/Test/test.img removable=1"
python .pi-dev-helper.py run "dmesg -T | grep g_floppy | tail -5"
```

Expected: dmesg shows the throttle init log.

- [ ] **Step 7: Test that throttling visibly affects host I/O speed**

On the host PC, mount the device and time a read of the entire 1.44MB image (e.g. `time dd if=/dev/sdX of=/dev/null bs=512 count=2880` on Linux host, or similar on Windows).

With `floppy-real` preset (50 KB/s read), the read should take ~30 seconds.

```bash
# To compare, switch to unthrottled at runtime (can't yet — sysfs not wired) or
# reload with subclass=scsi (Phase 1 had unbounded speed).
# For now we just verify the SLOW behavior of the default preset.
```

- [ ] **Step 8: Cleanup, append diff, commit**

```bash
python .pi-dev-helper.py sudo "rmmod g_floppy; systemctl start usb-floppy-pi"

cat >> kernel/UPSTREAM-DIFF.md <<'EOF'
- 2026-05-07: Added kernel/floppy_throttle.{c,h} implementing 3 named presets
  (floppy-real, floppy-fast, unthrottled) with usleep_range-based pacing and
  CHS-derived seek detection. Hooked from f_floppy.c do_read/do_write.
EOF

git add kernel/floppy_throttle.h kernel/floppy_throttle.c kernel/Makefile kernel/f_floppy.c kernel/g_floppy_main.c kernel/UPSTREAM-DIFF.md
git commit -m "feat(kernel): floppy_throttle.c with 3 presets, default floppy-real (~50 KB/s)"
```

---

## Task 10: Add sysfs `speed_preset` attribute

Make the throttle preset settable at runtime.

**Files:**
- Modify: `kernel/g_floppy_main.c`
- Modify: `kernel/UPSTREAM-DIFF.md`

- [ ] **Step 1: Add the sysfs attribute callback**

In `kernel/g_floppy_main.c`, near the other lun0_* attrs, add:

```c
#include "floppy_throttle.h"

static ssize_t speed_preset_show(struct device *d, struct device_attribute *a, char *buf)
{
    struct floppy_throttle_state *st = floppy_throttle_get();
    if (!st)
        return scnprintf(buf, PAGE_SIZE, "(uninitialized)\n");
    return floppy_throttle_show_preset(st, buf);
}

static ssize_t speed_preset_store(struct device *d, struct device_attribute *a,
                                   const char *buf, size_t count)
{
    struct floppy_throttle_state *st = floppy_throttle_get();
    int err;
    if (!st)
        return -ENODEV;
    err = floppy_throttle_set_preset(st, buf);
    if (err)
        return err;
    return count;
}
static DEVICE_ATTR_RW(speed_preset);

static ssize_t speed_read_kbps_show(struct device *d, struct device_attribute *a, char *buf)
{
    struct floppy_throttle_state *st = floppy_throttle_get();
    if (!st) return scnprintf(buf, PAGE_SIZE, "0\n");
    return scnprintf(buf, PAGE_SIZE, "%u\n", floppy_throttle_read_kbps(st));
}
static DEVICE_ATTR_RO(speed_read_kbps);

static ssize_t speed_write_kbps_show(struct device *d, struct device_attribute *a, char *buf)
{
    struct floppy_throttle_state *st = floppy_throttle_get();
    if (!st) return scnprintf(buf, PAGE_SIZE, "0\n");
    return scnprintf(buf, PAGE_SIZE, "%u\n", floppy_throttle_write_kbps(st));
}
static DEVICE_ATTR_RO(speed_write_kbps);

static ssize_t seek_us_show(struct device *d, struct device_attribute *a, char *buf)
{
    struct floppy_throttle_state *st = floppy_throttle_get();
    if (!st) return scnprintf(buf, PAGE_SIZE, "0\n");
    return scnprintf(buf, PAGE_SIZE, "%u\n", floppy_throttle_seek_us(st));
}
static DEVICE_ATTR_RO(seek_us);
```

Update `usb_floppy_attrs[]`:

```c
static struct attribute *usb_floppy_attrs[] = {
    &dev_attr_lun0_file.attr,
    &dev_attr_lun0_ro.attr,
    &dev_attr_lun0_inquiry_string.attr,
    &dev_attr_speed_preset.attr,
    &dev_attr_speed_read_kbps.attr,
    &dev_attr_speed_write_kbps.attr,
    &dev_attr_seek_us.attr,
    NULL,
};
```

- [ ] **Step 2: Add a module param to set the preset at load time**

Near the top of `g_floppy_main.c`:

```c
static char *speed_preset_param = "floppy-real";
module_param_named(speed_preset, speed_preset_param, charp, 0444);
MODULE_PARM_DESC(speed_preset, "Initial speed preset: floppy-real | floppy-fast | unthrottled");
```

In the module init (where you call `floppy_throttle_init`), after the init succeeds:

```c
floppy_throttle_set_preset(floppy_throttle_get(), speed_preset_param);
```

- [ ] **Step 3: Build, upload, reload, test**

```bash
python -c "
import paramiko
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect('floppyusb', username='pi', password='floppy', look_for_keys=False, allow_agent=False)
sftp = c.open_sftp()
sftp.put('kernel/g_floppy_main.c', '/home/pi/kernel-dev/kernel/g_floppy_main.c')
sftp.close(); c.close()
"
python .pi-dev-helper.py run "cd /home/pi/kernel-dev/kernel && make 2>&1 | tail -5"
python .pi-dev-helper.py sudo "rmmod g_floppy 2>/dev/null; insmod /home/pi/kernel-dev/kernel/g_floppy.ko file=/home/pi/floppies/Test/test.img speed_preset=floppy-fast removable=1"
python .pi-dev-helper.py run "cat /sys/class/usb_floppy/usb-floppy-pi/speed_preset /sys/class/usb_floppy/usb-floppy-pi/speed_read_kbps"
```

Expected:
```
floppy-fast
200
```

```bash
python .pi-dev-helper.py sudo "echo unthrottled > /sys/class/usb_floppy/usb-floppy-pi/speed_preset"
python .pi-dev-helper.py run "cat /sys/class/usb_floppy/usb-floppy-pi/speed_preset /sys/class/usb_floppy/usb-floppy-pi/speed_read_kbps"
```

Expected:
```
unthrottled
0
```

- [ ] **Step 4: Cleanup, commit**

```bash
python .pi-dev-helper.py sudo "rmmod g_floppy"

cat >> kernel/UPSTREAM-DIFF.md <<'EOF'
- 2026-05-07: g_floppy_main.c gained sysfs speed_preset (rw) plus three derived
  read-only attributes (speed_read_kbps, speed_write_kbps, seek_us). Added
  module param speed_preset for load-time configuration.
EOF

git add kernel/g_floppy_main.c kernel/UPSTREAM-DIFF.md
git commit -m "feat(kernel): /sys/class/usb_floppy/speed_preset (rw) + read-only derivatives"
```

---

## Task 11: Add PWM device tree overlay + verify pwm_request works

Before implementing the buzzer driver, ensure the PWM hardware is accessible from kernel space.

**Files:**
- Modify: `deploy/boot/config.txt.append` — add `dtoverlay=pwm,pin=18,func=2`

- [ ] **Step 1: Add the overlay to our config.txt patch**

Edit `deploy/boot/config.txt.append`:

```
# === usb-floppy-pi additions ===
dtoverlay=dwc2
dtparam=i2c_arm=on
dtparam=audio=off
dtoverlay=pwm,pin=18,func=2
# === end usb-floppy-pi ===
```

- [ ] **Step 2: Apply the change to the live Pi**

```bash
python .pi-dev-helper.py sudo 'grep -q "dtoverlay=pwm" /boot/firmware/config.txt || sed -i "/=== usb-floppy-pi additions ===/a dtoverlay=pwm,pin=18,func=2" /boot/firmware/config.txt'
python .pi-dev-helper.py run "grep -A1 -B1 'pwm' /boot/firmware/config.txt"
```

Expected: shows the `dtoverlay=pwm,pin=18,func=2` line.

- [ ] **Step 3: Reboot and verify PWM is exposed**

```bash
python .pi-dev-helper.py sudo "reboot"
```

Wait ~30 seconds, then:

```bash
python .pi-dev-helper.py run "ls /sys/class/pwm/"
```

Expected: shows `pwmchip0/`.

```bash
python .pi-dev-helper.py run "ls /sys/class/pwm/pwmchip0/"
```

Expected: shows files like `device`, `export`, `npwm`, etc. `cat /sys/class/pwm/pwmchip0/npwm` should be `>= 1`.

- [ ] **Step 4: Test PWM from userspace (sanity check before kernel module work)**

```bash
python .pi-dev-helper.py sudo 'echo 0 > /sys/class/pwm/pwmchip0/export 2>&1 || echo "already exported"'
python .pi-dev-helper.py sudo 'echo 1000000 > /sys/class/pwm/pwmchip0/pwm0/period && echo 500000 > /sys/class/pwm/pwmchip0/pwm0/duty_cycle && echo 1 > /sys/class/pwm/pwmchip0/pwm0/enable'
```

If a piezo buzzer is connected to GPIO 18 and GND, you should hear a 1kHz tone.

```bash
python .pi-dev-helper.py sudo 'echo 0 > /sys/class/pwm/pwmchip0/pwm0/enable && echo 0 > /sys/class/pwm/pwmchip0/unexport'
```

- [ ] **Step 5: Commit the deploy change**

```bash
cd D:/Projects/Personal/usb-floppy-pi
git add deploy/boot/config.txt.append
git commit -m "deploy(boot): add dtoverlay=pwm,pin=18,func=2 for kernel-side buzzer (Phase 2.4)"
```

---

## Task 12: Implement `floppy_buzzer.c` sound primitives

Build the buzzer module's foundation: PWM acquisition, basic tone playback, simple chirp.

**Files:**
- Create: `kernel/floppy_buzzer.h`
- Create: `kernel/floppy_buzzer.c`
- Modify: `kernel/Makefile` — add `floppy_buzzer.o`
- Modify: `kernel/UPSTREAM-DIFF.md`

- [ ] **Step 1: Write `kernel/floppy_buzzer.h`**

```c
#ifndef FLOPPY_BUZZER_H
#define FLOPPY_BUZZER_H

#include <linux/types.h>
#include <linux/spinlock.h>
#include <linux/hrtimer.h>
#include <linux/kthread.h>

enum motor_state {
    MOTOR_IDLE,
    MOTOR_SPIN_UP,
    MOTOR_RUNNING,
    MOTOR_SPIN_DOWN,
};

struct sound_step {
    u32 freq_hz;          /* 0 = silence */
    u32 duration_us;
    u32 chirp_target_hz;  /* 0 = fixed tone, !=0 = linear chirp to this freq */
};

struct floppy_buzzer_state {
    struct pwm_device *pwm;
    struct hrtimer scheduler;
    struct kthread_worker *worker;
    struct kthread_work scheduler_work;

    bool enabled;
    bool mute;
    u32 volume;          /* 0..100 */

    enum motor_state state;
    ktime_t last_io;
    u32 last_track;

    const struct sound_step *active_seq;
    int active_seq_len;
    int active_pos;

    spinlock_t lock;
};

int  floppy_buzzer_init(struct floppy_buzzer_state *st);
void floppy_buzzer_exit(struct floppy_buzzer_state *st);

/* I/O event hook from f_floppy.c */
void floppy_buzzer_on_io(struct floppy_buzzer_state *st,
                          u32 lba, u32 nblocks, bool is_write);
void floppy_buzzer_on_eject(struct floppy_buzzer_state *st);

/* Configuration */
int  floppy_buzzer_set_mute(struct floppy_buzzer_state *st, bool mute);
int  floppy_buzzer_set_volume(struct floppy_buzzer_state *st, u32 volume);
int  floppy_buzzer_set_enabled(struct floppy_buzzer_state *st, bool enabled);

bool floppy_buzzer_is_mute(struct floppy_buzzer_state *st);
u32  floppy_buzzer_get_volume(struct floppy_buzzer_state *st);
bool floppy_buzzer_is_enabled(struct floppy_buzzer_state *st);

/* Singleton accessor for sysfs callbacks */
struct floppy_buzzer_state *floppy_buzzer_get(void);

#endif /* FLOPPY_BUZZER_H */
```

- [ ] **Step 2: Write `kernel/floppy_buzzer.c` (initial version: tones + chirps, no state machine yet)**

```c
#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/pwm.h>
#include <linux/hrtimer.h>
#include <linux/kthread.h>
#include <linux/delay.h>
#include "floppy_buzzer.h"

static struct floppy_buzzer_state *g_buzzer;

/* Convert volume (0..100) and frequency (Hz) into PWM duty cycle (ns).
 * Maximum duty for piezo = 50% (square wave); volume scales it linearly. */
static u64 vol_to_duty_ns(u32 volume, u64 period_ns)
{
    if (volume == 0) return 0;
    if (volume > 100) volume = 100;
    return (period_ns * volume) / 200;  /* 50% max → /200 = volume/200 */
}

static void buzzer_apply_tone(struct floppy_buzzer_state *st,
                               u32 freq_hz)
{
    struct pwm_state pstate;
    u64 period_ns;
    if (!st->pwm) return;
    if (st->mute || !st->enabled || freq_hz == 0) {
        pwm_get_state(st->pwm, &pstate);
        pstate.enabled = false;
        pwm_apply_state(st->pwm, &pstate);
        return;
    }
    period_ns = 1000000000ULL / freq_hz;
    pstate.period = period_ns;
    pstate.duty_cycle = vol_to_duty_ns(st->volume, period_ns);
    pstate.polarity = PWM_POLARITY_NORMAL;
    pstate.enabled = true;
    pwm_apply_state(st->pwm, &pstate);
}

static void buzzer_silence(struct floppy_buzzer_state *st)
{
    buzzer_apply_tone(st, 0);
}

/* Scheduler work — advance the active sequence. */
static void buzzer_scheduler_work(struct kthread_work *work)
{
    struct floppy_buzzer_state *st = container_of(work, struct floppy_buzzer_state, scheduler_work);
    const struct sound_step *step;
    unsigned long flags;
    u32 freq_to_play;

    spin_lock_irqsave(&st->lock, flags);
    if (!st->active_seq || st->active_pos >= st->active_seq_len) {
        st->active_seq = NULL;
        spin_unlock_irqrestore(&st->lock, flags);
        buzzer_silence(st);
        return;
    }
    step = &st->active_seq[st->active_pos];
    freq_to_play = step->freq_hz;
    st->active_pos++;
    spin_unlock_irqrestore(&st->lock, flags);

    if (step->chirp_target_hz != 0 && step->freq_hz != 0) {
        /* Decompose chirp into 30 micro-steps for smoothness. */
        const u32 N = 30;
        u32 i;
        u32 step_us = step->duration_us / N;
        s64 from = step->freq_hz, to = step->chirp_target_hz;
        for (i = 0; i < N; i++) {
            u32 freq = from + ((to - from) * i / (s64)(N - 1));
            buzzer_apply_tone(st, freq);
            usleep_range(step_us, step_us + 100);
        }
    } else if (freq_to_play == 0) {
        buzzer_silence(st);
        usleep_range(step->duration_us, step->duration_us + 100);
    } else {
        buzzer_apply_tone(st, freq_to_play);
        usleep_range(step->duration_us, step->duration_us + 100);
    }

    /* Re-queue ourselves for the next step. */
    kthread_queue_work(st->worker, &st->scheduler_work);
}

void floppy_buzzer_play(struct floppy_buzzer_state *st,
                         const struct sound_step *seq, int len)
{
    unsigned long flags;
    spin_lock_irqsave(&st->lock, flags);
    st->active_seq = seq;
    st->active_seq_len = len;
    st->active_pos = 0;
    spin_unlock_irqrestore(&st->lock, flags);
    kthread_queue_work(st->worker, &st->scheduler_work);
}

int floppy_buzzer_init(struct floppy_buzzer_state *st)
{
    st->pwm = pwm_request(0, "g_floppy");
    if (IS_ERR_OR_NULL(st->pwm)) {
        pr_warn("g_floppy: pwm_request(0) failed; buzzer will be silent\n");
        st->pwm = NULL;
        /* Continue init anyway; buzzer just won't emit. */
    }

    spin_lock_init(&st->lock);
    st->enabled = true;
    st->mute = false;
    st->volume = 70;
    st->state = MOTOR_IDLE;
    st->last_io = ktime_get();
    st->last_track = ~0u;

    st->worker = kthread_create_worker(0, "g_floppy_buzzer");
    if (IS_ERR(st->worker)) {
        pr_err("g_floppy: failed to create buzzer kthread\n");
        if (st->pwm) pwm_free(st->pwm);
        return PTR_ERR(st->worker);
    }
    kthread_init_work(&st->scheduler_work, buzzer_scheduler_work);

    g_buzzer = st;
    pr_info("g_floppy: buzzer initialized (PWM %s)\n",
            st->pwm ? "claimed" : "unavailable; silent");
    return 0;
}

void floppy_buzzer_exit(struct floppy_buzzer_state *st)
{
    if (st->worker) {
        kthread_flush_worker(st->worker);
        kthread_destroy_worker(st->worker);
    }
    if (st->pwm) {
        buzzer_silence(st);
        pwm_free(st->pwm);
    }
    g_buzzer = NULL;
}

struct floppy_buzzer_state *floppy_buzzer_get(void) { return g_buzzer; }

int floppy_buzzer_set_mute(struct floppy_buzzer_state *st, bool mute)
{
    st->mute = mute;
    if (mute) buzzer_silence(st);
    return 0;
}

int floppy_buzzer_set_volume(struct floppy_buzzer_state *st, u32 volume)
{
    if (volume > 100) volume = 100;
    st->volume = volume;
    return 0;
}

int floppy_buzzer_set_enabled(struct floppy_buzzer_state *st, bool enabled)
{
    st->enabled = enabled;
    if (!enabled) buzzer_silence(st);
    return 0;
}

bool floppy_buzzer_is_mute(struct floppy_buzzer_state *st) { return st->mute; }
u32  floppy_buzzer_get_volume(struct floppy_buzzer_state *st) { return st->volume; }
bool floppy_buzzer_is_enabled(struct floppy_buzzer_state *st) { return st->enabled; }

/* I/O hooks — Task 13 will fill these in with the state machine. For now: stubs. */
void floppy_buzzer_on_io(struct floppy_buzzer_state *st,
                         u32 lba, u32 nblocks, bool is_write)
{
    /* No-op until Task 13. */
    (void)lba; (void)nblocks; (void)is_write;
}

void floppy_buzzer_on_eject(struct floppy_buzzer_state *st)
{
    /* No-op until Task 13. */
}
```

- [ ] **Step 3: Update Makefile**

Change `g_floppy-y` to add `floppy_buzzer.o`:

```
g_floppy-y := g_floppy_main.o f_floppy.o storage_common.o \
              floppy_throttle.o floppy_buzzer.o
```

- [ ] **Step 4: Test playback of a hardcoded chirp**

In `g_floppy_main.c`, in the module init (after `floppy_buzzer_init`), add a one-shot test sequence to play at load:

```c
{
    static const struct sound_step boot_chirp[] = {
        { 200, 600000, 500 },  /* chirp 200→500 Hz over 600ms */
    };
    floppy_buzzer_play(floppy_buzzer_get(), boot_chirp, ARRAY_SIZE(boot_chirp));
}
```

Build, upload, reload:

```bash
python -c "
import paramiko
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect('floppyusb', username='pi', password='floppy', look_for_keys=False, allow_agent=False)
sftp = c.open_sftp()
for f in ['Makefile', 'floppy_buzzer.h', 'floppy_buzzer.c', 'g_floppy_main.c']:
    sftp.put('kernel/' + f, '/home/pi/kernel-dev/kernel/' + f)
    print('uploaded', f)
sftp.close(); c.close()
"
python .pi-dev-helper.py run "cd /home/pi/kernel-dev/kernel && make 2>&1 | tail -5"
python .pi-dev-helper.py sudo "rmmod g_floppy 2>/dev/null; insmod /home/pi/kernel-dev/kernel/g_floppy.ko file=/home/pi/floppies/Test/test.img removable=1"
python .pi-dev-helper.py run "dmesg -T | grep -i buzzer | tail -3"
```

If a piezo is connected to GPIO 18 + GND, you should hear a chirp from 200Hz rising to 500Hz over ~0.6 seconds.

- [ ] **Step 5: Cleanup, commit**

Remove the test chirp from the init code (we'll hook real events in Task 13). Verify the file is clean.

```bash
python .pi-dev-helper.py sudo "rmmod g_floppy; systemctl start usb-floppy-pi"

cat >> kernel/UPSTREAM-DIFF.md <<'EOF'
- 2026-05-07: Added kernel/floppy_buzzer.{c,h}: pwm_request, hrtimer-driven
  sound playback, sound_step sequences with chirp support, mute/volume/enable
  knobs. I/O hooks present but no-op (Task 13 wires the state machine).
EOF

git add kernel/floppy_buzzer.h kernel/floppy_buzzer.c kernel/Makefile kernel/g_floppy_main.c kernel/UPSTREAM-DIFF.md
git commit -m "feat(kernel): floppy_buzzer.c with PWM-based tones and chirps via kthread+hrtimer"
```

---

## Task 13: Implement buzzer state machine + hook I/O events

Now the buzzer plays real sound sequences in response to I/O activity, including motor spin-up/down and seek clacks.

**Files:**
- Modify: `kernel/floppy_buzzer.c`
- Modify: `kernel/f_floppy.c` — add `floppy_buzzer_on_io` calls next to throttle hooks
- Modify: `kernel/UPSTREAM-DIFF.md`

- [ ] **Step 1: Define the sound sequences in `floppy_buzzer.c`**

Above `buzzer_scheduler_work`, add:

```c
/* === Sound sequences (compile-time constants) === */

static const struct sound_step seq_spin_up[] = {
    { 200, 600000, 500 },          /* chirp 200→500 Hz over 600ms */
};

static const struct sound_step seq_head_load[] = {
    { 150, 60000, 0 },             /* clack: 60ms tone at 150 Hz */
    { 0, 30000, 0 },               /* brief silence */
};

static const struct sound_step seq_clack[] = {
    { 150, 60000, 0 },
    { 0, 20000, 0 },
};

static const struct sound_step seq_multi_clack[] = {
    { 150, 60000, 0 }, { 0, 80000, 0 },
    { 150, 60000, 0 }, { 0, 80000, 0 },
    { 150, 60000, 0 }, { 0, 20000, 0 },
};

static const struct sound_step seq_spin_down[] = {
    { 400, 800000, 100 },          /* chirp 400→100 Hz over 800ms */
    { 0, 50000, 0 },
};

static const struct sound_step seq_eject[] = {
    { 400, 600000, 100 },          /* spin down */
    { 50, 100000, 0 },             /* mechanical "tick" at 50 Hz */
    { 0, 50000, 0 },
};
```

- [ ] **Step 2: Write the I/O event handler with state machine**

Replace the no-op `floppy_buzzer_on_io` with:

```c
#define IDLE_THRESHOLD_NS (3LL * NSEC_PER_SEC)
#define SECTORS_PER_TRACK 36

static void play_locked(struct floppy_buzzer_state *st,
                         const struct sound_step *seq, int len)
{
    /* Caller holds st->lock. Sets the active sequence; the worker will
     * pick it up on the next iteration. */
    st->active_seq = seq;
    st->active_seq_len = len;
    st->active_pos = 0;
    kthread_queue_work(st->worker, &st->scheduler_work);
}

void floppy_buzzer_on_io(struct floppy_buzzer_state *st,
                         u32 lba, u32 nblocks, bool is_write)
{
    unsigned long flags;
    ktime_t now = ktime_get();
    s64 idle_ns;
    u32 cur_track;
    u32 track_delta;

    if (!st || !st->enabled || st->mute) return;

    spin_lock_irqsave(&st->lock, flags);
    idle_ns = ktime_to_ns(ktime_sub(now, st->last_io));
    cur_track = lba / SECTORS_PER_TRACK;
    track_delta = (cur_track > st->last_track) ?
                   (cur_track - st->last_track) :
                   (st->last_track - cur_track);

    if (st->state == MOTOR_IDLE || st->state == MOTOR_SPIN_DOWN ||
        idle_ns > IDLE_THRESHOLD_NS) {
        /* Cold start: spin up + head load. */
        play_locked(st, seq_spin_up, ARRAY_SIZE(seq_spin_up));
        st->state = MOTOR_RUNNING;
    } else if (st->last_track != ~0u && track_delta > 5) {
        /* Big seek: emit a multi-clack burst. */
        play_locked(st, seq_multi_clack, ARRAY_SIZE(seq_multi_clack));
    } else if (st->last_track != ~0u && cur_track != st->last_track) {
        /* Single-track seek. */
        play_locked(st, seq_clack, ARRAY_SIZE(seq_clack));
    }
    /* Else: same track, no sound — just continue running. */

    st->last_io = now;
    st->last_track = cur_track;
    spin_unlock_irqrestore(&st->lock, flags);
}

void floppy_buzzer_on_eject(struct floppy_buzzer_state *st)
{
    unsigned long flags;
    if (!st || !st->enabled || st->mute) return;
    spin_lock_irqsave(&st->lock, flags);
    play_locked(st, seq_eject, ARRAY_SIZE(seq_eject));
    st->state = MOTOR_IDLE;
    spin_unlock_irqrestore(&st->lock, flags);
}
```

- [ ] **Step 3: Add a periodic check for idle → spin-down transition**

Add to `floppy_buzzer.c`, after the existing `floppy_buzzer_init`:

```c
static struct hrtimer idle_timer;

static enum hrtimer_restart idle_check_callback(struct hrtimer *t)
{
    struct floppy_buzzer_state *st = floppy_buzzer_get();
    unsigned long flags;
    if (!st) goto out;

    spin_lock_irqsave(&st->lock, flags);
    if (st->state == MOTOR_RUNNING) {
        s64 idle_ns = ktime_to_ns(ktime_sub(ktime_get(), st->last_io));
        if (idle_ns > IDLE_THRESHOLD_NS) {
            play_locked(st, seq_spin_down, ARRAY_SIZE(seq_spin_down));
            st->state = MOTOR_IDLE;
        }
    }
    spin_unlock_irqrestore(&st->lock, flags);
out:
    hrtimer_forward_now(t, ms_to_ktime(500));
    return HRTIMER_RESTART;
}
```

In `floppy_buzzer_init`, before the `return 0`:

```c
hrtimer_init(&idle_timer, CLOCK_MONOTONIC, HRTIMER_MODE_REL);
idle_timer.function = idle_check_callback;
hrtimer_start(&idle_timer, ms_to_ktime(500), HRTIMER_MODE_REL);
```

In `floppy_buzzer_exit`, before destroying the worker:

```c
hrtimer_cancel(&idle_timer);
```

- [ ] **Step 4: Hook into `f_floppy.c`**

In `kernel/f_floppy.c`, where you added `floppy_throttle_on_read` and `_on_write` (Task 9), also add `floppy_buzzer_on_io`:

```c
#include "floppy_buzzer.h"
...
floppy_throttle_on_read(floppy_throttle_get(), lba, nblocks);
floppy_buzzer_on_io(floppy_buzzer_get(), lba, nblocks, false);
```

And in `do_write`:

```c
floppy_throttle_on_write(floppy_throttle_get(), lba, nblocks);
floppy_buzzer_on_io(floppy_buzzer_get(), lba, nblocks, true);
```

For the eject hook: in `f_floppy.c`, find `fsg_lun_close` (or where the LUN file is closed). After it succeeds, add:

```c
floppy_buzzer_on_eject(floppy_buzzer_get());
```

- [ ] **Step 5: Build, upload, reload, validate**

```bash
python -c "
import paramiko
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect('floppyusb', username='pi', password='floppy', look_for_keys=False, allow_agent=False)
sftp = c.open_sftp()
for f in ['floppy_buzzer.c', 'f_floppy.c']:
    sftp.put('kernel/' + f, '/home/pi/kernel-dev/kernel/' + f)
    print('uploaded', f)
sftp.close(); c.close()
"
python .pi-dev-helper.py run "cd /home/pi/kernel-dev/kernel && make 2>&1 | tail -5"
python .pi-dev-helper.py sudo "rmmod g_floppy 2>/dev/null; insmod /home/pi/kernel-dev/kernel/g_floppy.ko file=/home/pi/floppies/Test/test.img removable=1"
```

Connect host PC. Read the floppy contents (or copy something). With piezo connected, you should hear:
1. **Spin-up chirp** (rising tone) on first I/O
2. **Clack** when track changes during reads
3. **Spin-down chirp** (falling tone) ~3 seconds after last I/O

Eject by writing empty to lun0_file:

```bash
python .pi-dev-helper.py sudo "echo > /sys/class/usb_floppy/usb-floppy-pi/lun0_file"
```

Should hear the eject sequence (spin down + tick).

- [ ] **Step 6: Cleanup, commit**

```bash
python .pi-dev-helper.py sudo "rmmod g_floppy"

cat >> kernel/UPSTREAM-DIFF.md <<'EOF'
- 2026-05-07: floppy_buzzer.c gained sound sequences (spin_up, head_load,
  clack, multi_clack, spin_down, eject) plus motor state machine
  (IDLE/SPIN_UP/RUNNING/SPIN_DOWN) and a 500ms hrtimer for idle→spin_down
  transition. f_floppy.c calls floppy_buzzer_on_io from do_read/do_write
  and floppy_buzzer_on_eject from fsg_lun_close.
EOF

git add kernel/floppy_buzzer.c kernel/f_floppy.c kernel/UPSTREAM-DIFF.md
git commit -m "feat(kernel): buzzer state machine — spin-up/clack/spin-down sounds during I/O"
```

---

## Task 14: Add sysfs `buzzer`, `mute`, `volume` attributes

Make the buzzer controllable at runtime.

**Files:**
- Modify: `kernel/g_floppy_main.c`

- [ ] **Step 1: Add the attribute callbacks**

In `kernel/g_floppy_main.c`, add `#include "floppy_buzzer.h"` near the existing `#include "floppy_throttle.h"`. Then add the attribute show/store functions:

```c
static ssize_t buzzer_show(struct device *d, struct device_attribute *a, char *buf)
{
    struct floppy_buzzer_state *st = floppy_buzzer_get();
    if (!st) return scnprintf(buf, PAGE_SIZE, "0\n");
    return scnprintf(buf, PAGE_SIZE, "%d\n", floppy_buzzer_is_enabled(st) ? 1 : 0);
}
static ssize_t buzzer_store(struct device *d, struct device_attribute *a,
                             const char *buf, size_t count)
{
    struct floppy_buzzer_state *st = floppy_buzzer_get();
    int val;
    if (!st) return -ENODEV;
    if (kstrtoint(buf, 10, &val) < 0) return -EINVAL;
    floppy_buzzer_set_enabled(st, val != 0);
    return count;
}
static DEVICE_ATTR_RW(buzzer);

static ssize_t mute_show(struct device *d, struct device_attribute *a, char *buf)
{
    struct floppy_buzzer_state *st = floppy_buzzer_get();
    if (!st) return scnprintf(buf, PAGE_SIZE, "0\n");
    return scnprintf(buf, PAGE_SIZE, "%d\n", floppy_buzzer_is_mute(st) ? 1 : 0);
}
static ssize_t mute_store(struct device *d, struct device_attribute *a,
                           const char *buf, size_t count)
{
    struct floppy_buzzer_state *st = floppy_buzzer_get();
    int val;
    if (!st) return -ENODEV;
    if (kstrtoint(buf, 10, &val) < 0) return -EINVAL;
    floppy_buzzer_set_mute(st, val != 0);
    return count;
}
static DEVICE_ATTR_RW(mute);

static ssize_t volume_show(struct device *d, struct device_attribute *a, char *buf)
{
    struct floppy_buzzer_state *st = floppy_buzzer_get();
    if (!st) return scnprintf(buf, PAGE_SIZE, "0\n");
    return scnprintf(buf, PAGE_SIZE, "%u\n", floppy_buzzer_get_volume(st));
}
static ssize_t volume_store(struct device *d, struct device_attribute *a,
                             const char *buf, size_t count)
{
    struct floppy_buzzer_state *st = floppy_buzzer_get();
    u32 val;
    if (!st) return -ENODEV;
    if (kstrtouint(buf, 10, &val) < 0) return -EINVAL;
    if (val > 100) val = 100;
    floppy_buzzer_set_volume(st, val);
    return count;
}
static DEVICE_ATTR_RW(volume);
```

Update `usb_floppy_attrs[]`:

```c
static struct attribute *usb_floppy_attrs[] = {
    &dev_attr_lun0_file.attr,
    &dev_attr_lun0_ro.attr,
    &dev_attr_lun0_inquiry_string.attr,
    &dev_attr_speed_preset.attr,
    &dev_attr_speed_read_kbps.attr,
    &dev_attr_speed_write_kbps.attr,
    &dev_attr_seek_us.attr,
    &dev_attr_buzzer.attr,
    &dev_attr_mute.attr,
    &dev_attr_volume.attr,
    NULL,
};
```

- [ ] **Step 2: Add module params + init for buzzer**

Near the top of `g_floppy_main.c`, add:

```c
static int buzzer_param = 1;
static int volume_param = 70;
static int mute_param = 0;
module_param_named(buzzer, buzzer_param, int, 0444);
module_param_named(volume, volume_param, int, 0444);
module_param_named(mute, mute_param, int, 0444);
MODULE_PARM_DESC(buzzer, "Enable buzzer sounds at load (1) or disable (0)");
MODULE_PARM_DESC(volume, "Buzzer volume 0..100 at load (default 70)");
MODULE_PARM_DESC(mute, "Mute buzzer at load (default 0 = unmuted)");
```

In the module init (after `floppy_buzzer_init` succeeds), apply the params:

```c
floppy_buzzer_set_enabled(floppy_buzzer_get(), buzzer_param != 0);
floppy_buzzer_set_volume(floppy_buzzer_get(), volume_param);
floppy_buzzer_set_mute(floppy_buzzer_get(), mute_param != 0);
```

- [ ] **Step 3: Build, upload, reload, test**

```bash
python -c "
import paramiko
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect('floppyusb', username='pi', password='floppy', look_for_keys=False, allow_agent=False)
sftp = c.open_sftp()
sftp.put('kernel/g_floppy_main.c', '/home/pi/kernel-dev/kernel/g_floppy_main.c')
sftp.close(); c.close()
"
python .pi-dev-helper.py run "cd /home/pi/kernel-dev/kernel && make 2>&1 | tail -5"
python .pi-dev-helper.py sudo "rmmod g_floppy 2>/dev/null; insmod /home/pi/kernel-dev/kernel/g_floppy.ko file=/home/pi/floppies/Test/test.img volume=50 removable=1"
python .pi-dev-helper.py run "cat /sys/class/usb_floppy/usb-floppy-pi/volume /sys/class/usb_floppy/usb-floppy-pi/mute /sys/class/usb_floppy/usb-floppy-pi/buzzer"
```

Expected:
```
50
0
1
```

```bash
python .pi-dev-helper.py sudo "echo 1 > /sys/class/usb_floppy/usb-floppy-pi/mute"
# triggering I/O from host should now produce silence
python .pi-dev-helper.py sudo "echo 0 > /sys/class/usb_floppy/usb-floppy-pi/mute"
# I/O sounds again
```

- [ ] **Step 4: Commit**

```bash
python .pi-dev-helper.py sudo "rmmod g_floppy"

cat >> kernel/UPSTREAM-DIFF.md <<'EOF'
- 2026-05-07: g_floppy_main.c added sysfs buzzer, mute, volume attrs (rw)
  plus matching module params for load-time defaults.
EOF

git add kernel/g_floppy_main.c kernel/UPSTREAM-DIFF.md
git commit -m "feat(kernel): /sys/class/usb_floppy/{buzzer,mute,volume} runtime control"
```

---

## Task 15: DKMS packaging

Wrap the kernel module as DKMS so it survives kernel updates.

**Files:**
- Create: `kernel/dkms.conf`
- Create: `kernel/README.md`

- [ ] **Step 1: Write `kernel/dkms.conf`**

```
PACKAGE_NAME="g-floppy"
PACKAGE_VERSION="0.1.0"

BUILT_MODULE_NAME[0]="g_floppy"
BUILT_MODULE_LOCATION[0]="."
DEST_MODULE_LOCATION[0]="/extra"

MAKE[0]="make KDIR=/lib/modules/${kernelver}/build"
CLEAN="make clean"

AUTOINSTALL="yes"
```

- [ ] **Step 2: Write `kernel/README.md`**

```markdown
# g_floppy.ko — usb-floppy-pi kernel module

USB Mass Storage gadget specialized for floppy emulation. Replaces the upstream
`g_mass_storage` module for our use case. Adds:

- UFI subclass (`bInterfaceSubClass=0x04`) for true floppy identity in Windows
- Configurable speed throttling (presets: floppy-real, floppy-fast, unthrottled)
- HW PWM buzzer driving floppy emulation sounds (spin-up, clacks, spin-down)

## Build out-of-tree

```
make KDIR=/lib/modules/$(uname -r)/build
sudo insmod ./g_floppy.ko file=/path/to/disk.img removable=1
```

## Install as DKMS

```
sudo apt install dkms raspberrypi-kernel-headers
sudo cp -r kernel /usr/src/g-floppy-0.1.0
sudo dkms add -m g-floppy -v 0.1.0
sudo dkms build -m g-floppy -v 0.1.0
sudo dkms install -m g-floppy -v 0.1.0
```

After install, `modprobe g_floppy` works system-wide.

## Module parameters

| param | default | description |
|-------|---------|-------------|
| `file` | (empty) | Path to backing image file |
| `ro` | 0 | Read-only flag |
| `removable` | 1 | Removable media flag (must be 1 for floppy emulation) |
| `stall` | 0 | Bulk endpoint stall (Windows works better with 0) |
| `subclass` | "ufi" | "ufi" or "scsi" — fallback if BIOS doesn't accept UFI |
| `speed_preset` | "floppy-real" | floppy-real / floppy-fast / unthrottled |
| `buzzer` | 1 | Enable buzzer sounds |
| `volume` | 70 | 0..100 |
| `mute` | 0 | Override at load |

## Sysfs runtime interface

`/sys/class/usb_floppy/usb-floppy-pi/`:

- `lun0_file` (rw) — backing file; write empty to eject
- `lun0_ro` (rw) — read-only flag
- `lun0_inquiry_string` (rw) — SCSI INQUIRY string (28 chars)
- `speed_preset` (rw) — preset name
- `speed_read_kbps`, `speed_write_kbps`, `seek_us` (ro) — derived
- `buzzer`, `mute`, `volume` (rw)

## Source provenance

See `UPSTREAM-DIFF.md` for which upstream files we forked and what changes
we made on top of them.
```

- [ ] **Step 3: Test DKMS install on the Pi**

```bash
# Sync kernel/ dir (now with dkms.conf)
python -c "
import paramiko, glob, os
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect('floppyusb', username='pi', password='floppy', look_for_keys=False, allow_agent=False)
sftp = c.open_sftp()
# Upload everything in kernel/ (recursively into existing dirs)
for f in glob.glob('kernel/*.c') + glob.glob('kernel/*.h') + ['kernel/Makefile', 'kernel/dkms.conf', 'kernel/README.md', 'kernel/UPSTREAM-DIFF.md']:
    sftp.put(f, '/home/pi/kernel-dev/' + f.replace(chr(92), '/'))
    print('uploaded', f)
sftp.close(); c.close()
"

# Install DKMS package
python .pi-dev-helper.py sudo "rmmod g_floppy 2>/dev/null; rm -rf /usr/src/g-floppy-0.1.0; cp -r /home/pi/kernel-dev/kernel /usr/src/g-floppy-0.1.0"
python .pi-dev-helper.py sudo "dkms remove -m g-floppy -v 0.1.0 --all 2>/dev/null; dkms add -m g-floppy -v 0.1.0"
python .pi-dev-helper.py sudo "dkms build -m g-floppy -v 0.1.0 2>&1 | tail -10"
python .pi-dev-helper.py sudo "dkms install -m g-floppy -v 0.1.0 2>&1 | tail -5"
python .pi-dev-helper.py run "modinfo g_floppy 2>&1 | head -10"
```

Expected: `modinfo` shows the module is now system-wide. Modprobe works:

```bash
python .pi-dev-helper.py sudo "systemctl stop usb-floppy-pi; modprobe g_floppy file=/home/pi/floppies/Test/test.img removable=1"
python .pi-dev-helper.py run "lsmod | grep g_floppy"
```

- [ ] **Step 4: Cleanup test, commit**

```bash
python .pi-dev-helper.py sudo "rmmod g_floppy; systemctl start usb-floppy-pi"

cd D:/Projects/Personal/usb-floppy-pi
git add kernel/dkms.conf kernel/README.md
git commit -m "feat(kernel): DKMS packaging — survives kernel updates via apt-trigger autoinstall"
```

---

## Task 16: Implement Python `SysfsBackend`

Now the kernel module's user-facing interface is mature. Build the Python backend that talks to `/sys/class/usb_floppy/`.

**Files:**
- Create: `src/usb_floppy_pi/gadget/sysfs_backend.py`
- Modify: `src/usb_floppy_pi/gadget/backend.py` — extend Protocol with optional methods
- Test: `tests/unit/test_sysfs_backend.py`

- [ ] **Step 1: Write the failing tests**

Write `tests/unit/test_sysfs_backend.py`:

```python
"""Tests for gadget.sysfs_backend — uses tmp_path to simulate /sys/class/usb_floppy."""
from pathlib import Path

import pytest

from usb_floppy_pi.gadget.backend import GadgetParams
from usb_floppy_pi.gadget.sysfs_backend import SysfsBackend


def _params() -> GadgetParams:
    return GadgetParams(
        id_vendor=0x0525, id_product=0xa4a5, bcd_device=0x0001,
        manufacturer="Linux Foundation", product="USB Floppy", serial="0001",
        inquiry_string="TEAC    FD-05PUW         3000",
    )


@pytest.fixture
def fake_sysfs(tmp_path: Path):
    """Create a fake /sys/class/usb_floppy tree mirroring what the kernel exposes."""
    root = tmp_path / "usb_floppy" / "usb-floppy-pi"
    root.mkdir(parents=True)
    for name, default in [
        ("lun0_file", ""),
        ("lun0_ro", "0"),
        ("lun0_inquiry_string", "TEAC    FD-05PUW         3000"),
        ("speed_preset", "floppy-real"),
        ("speed_read_kbps", "50"),
        ("speed_write_kbps", "30"),
        ("seek_us", "6000"),
        ("buzzer", "1"),
        ("mute", "0"),
        ("volume", "70"),
    ]:
        (root / name).write_text(default + "\n")
    return root


def test_create_and_attach_are_noops(fake_sysfs: Path) -> None:
    backend = SysfsBackend(sysfs_root=fake_sysfs)
    backend.create_gadget(_params())   # must not raise
    backend.attach_to_udc()            # must not raise
    backend.detach_from_udc()
    backend.destroy_gadget()


def test_configure_lun_writes_file(fake_sysfs: Path) -> None:
    backend = SysfsBackend(sysfs_root=fake_sysfs)
    backend.configure_lun(file=Path("/home/pi/floppies/X/Y.img"), ro=False)
    assert (fake_sysfs / "lun0_file").read_text().strip() == "/home/pi/floppies/X/Y.img"
    assert (fake_sysfs / "lun0_ro").read_text().strip() == "0"


def test_configure_lun_eject_writes_newline(fake_sysfs: Path) -> None:
    (fake_sysfs / "lun0_file").write_text("/some/path.img\n")
    backend = SysfsBackend(sysfs_root=fake_sysfs)
    backend.configure_lun(file=None, ro=False)
    # We write "\n" (not "") because configfs/sysfs handlers ignore zero-byte writes
    assert (fake_sysfs / "lun0_file").read_text() == "\n"


def test_configure_lun_applies_ro(fake_sysfs: Path) -> None:
    backend = SysfsBackend(sysfs_root=fake_sysfs)
    backend.configure_lun(file=Path("/x.img"), ro=True)
    assert (fake_sysfs / "lun0_ro").read_text().strip() == "1"


def test_set_speed_preset(fake_sysfs: Path) -> None:
    backend = SysfsBackend(sysfs_root=fake_sysfs)
    backend.set_speed_preset("unthrottled")
    assert (fake_sysfs / "speed_preset").read_text().strip() == "unthrottled"


def test_set_volume(fake_sysfs: Path) -> None:
    backend = SysfsBackend(sysfs_root=fake_sysfs)
    backend.set_volume(45)
    assert (fake_sysfs / "volume").read_text().strip() == "45"


def test_set_mute(fake_sysfs: Path) -> None:
    backend = SysfsBackend(sysfs_root=fake_sysfs)
    backend.set_mute(True)
    assert (fake_sysfs / "mute").read_text().strip() == "1"
    backend.set_mute(False)
    assert (fake_sysfs / "mute").read_text().strip() == "0"


def test_set_buzzer_enabled(fake_sysfs: Path) -> None:
    backend = SysfsBackend(sysfs_root=fake_sysfs)
    backend.set_buzzer_enabled(False)
    assert (fake_sysfs / "buzzer").read_text().strip() == "0"


def test_get_metrics_reads_sysfs(fake_sysfs: Path) -> None:
    (fake_sysfs / "lun0_file").write_text("/x.img\n")
    backend = SysfsBackend(sysfs_root=fake_sysfs)
    metrics = backend.get_metrics()
    assert metrics["speed_preset"] == "floppy-real"
    assert metrics["speed_read_kbps"] == 50
    assert metrics["volume"] == 70
    assert metrics["mute"] is False
    assert metrics["lun0_file"] == "/x.img"
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd D:/Projects/Personal/usb-floppy-pi
.venv/Scripts/python -m pytest tests/unit/test_sysfs_backend.py -v
```

Expected: ModuleNotFoundError on `usb_floppy_pi.gadget.sysfs_backend`.

- [ ] **Step 3: Extend the GadgetBackend Protocol with optional methods**

Edit `src/usb_floppy_pi/gadget/backend.py`. After the existing Protocol definition, add new methods with default `pass` implementations so existing backends (`MockBackend`, `ConfigFsBackend`) still satisfy the Protocol:

```python
class GadgetBackend(Protocol):
    def create_gadget(self, params: GadgetParams) -> None: ...
    def destroy_gadget(self) -> None: ...
    def configure_lun(self, *, file: Path | None, ro: bool) -> None: ...
    def attach_to_udc(self) -> None: ...
    def detach_from_udc(self) -> None: ...

    # Optional Phase 2 capabilities — default no-op.
    def set_speed_preset(self, preset: str) -> None:
        return None

    def set_volume(self, volume: int) -> None:
        return None

    def set_mute(self, mute: bool) -> None:
        return None

    def set_buzzer_enabled(self, enabled: bool) -> None:
        return None

    def get_metrics(self) -> dict:
        return {}
```

(Note: Python's `Protocol` allows methods with bodies. They serve as defaults that conforming classes inherit if they don't override. Existing `MockBackend` and `ConfigFsBackend` automatically satisfy via these defaults.)

- [ ] **Step 4: Write the implementation**

Write `src/usb_floppy_pi/gadget/sysfs_backend.py`:

```python
"""Backend that talks to the kernel-side sysfs interface (/sys/class/usb_floppy).

Implements the GadgetBackend Protocol. The kernel module owns gadget creation,
UDC attachment, and the buzzer; this Python class only writes config to
sysfs attributes at runtime.
"""
from __future__ import annotations

import logging
from pathlib import Path

from .backend import GadgetParams

logger = logging.getLogger(__name__)


class SysfsBackend:
    """Backend for Phase 2 kernel module via /sys/class/usb_floppy/usb-floppy-pi."""

    DEFAULT_ROOT = Path("/sys/class/usb_floppy/usb-floppy-pi")

    def __init__(self, sysfs_root: Path | None = None) -> None:
        self._root = sysfs_root or self.DEFAULT_ROOT
        if not self._root.exists():
            raise FileNotFoundError(
                f"Kernel module sysfs not found at {self._root}; "
                "is g_floppy.ko loaded?"
            )

    def create_gadget(self, params: GadgetParams) -> None:
        # The kernel module created the gadget at module load. We just verify
        # the inquiry string matches what we'd expect, and update if it differs.
        cur = (self._root / "lun0_inquiry_string").read_text().strip()
        if cur != params.inquiry_string.strip():
            self._write("lun0_inquiry_string", params.inquiry_string)
            logger.info("updated inquiry_string to %r", params.inquiry_string)

    def destroy_gadget(self) -> None:
        # Module unloads independently (rmmod). Nothing to do at runtime.
        pass

    def configure_lun(self, *, file: Path | None, ro: bool) -> None:
        # Detach first (always — kernel rejects ro change while file is open).
        self._write("lun0_file", "\n")
        if file is None:
            return
        # Brief settle then set ro and attach new file.
        import time
        time.sleep(0.05)
        self._write("lun0_ro", "1" if ro else "0")
        self._write("lun0_file", str(file))

    def attach_to_udc(self) -> None:
        # Kernel auto-attaches at module load. Nothing to do.
        pass

    def detach_from_udc(self) -> None:
        # Idem.
        pass

    # Phase 2-specific capabilities.

    def set_speed_preset(self, preset: str) -> None:
        self._write("speed_preset", preset)

    def set_volume(self, volume: int) -> None:
        if volume < 0 or volume > 100:
            raise ValueError(f"volume must be 0..100, got {volume}")
        self._write("volume", str(volume))

    def set_mute(self, mute: bool) -> None:
        self._write("mute", "1" if mute else "0")

    def set_buzzer_enabled(self, enabled: bool) -> None:
        self._write("buzzer", "1" if enabled else "0")

    def get_metrics(self) -> dict:
        def read_int(name: str) -> int:
            return int((self._root / name).read_text().strip())

        def read_bool(name: str) -> bool:
            return (self._root / name).read_text().strip() == "1"

        def read_str(name: str) -> str:
            return (self._root / name).read_text().strip()

        return {
            "lun0_file": read_str("lun0_file"),
            "speed_preset": read_str("speed_preset"),
            "speed_read_kbps": read_int("speed_read_kbps"),
            "speed_write_kbps": read_int("speed_write_kbps"),
            "seek_us": read_int("seek_us"),
            "buzzer": read_bool("buzzer"),
            "mute": read_bool("mute"),
            "volume": read_int("volume"),
        }

    def _write(self, attr: str, value: str) -> None:
        (self._root / attr).write_text(value)
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
.venv/Scripts/python -m pytest tests/unit/test_sysfs_backend.py -v
```

Expected: 9 passed.

- [ ] **Step 6: Run full suite**

```bash
.venv/Scripts/python -m pytest -v
```

Expected: all tests pass (existing 78 + 9 new = 87).

- [ ] **Step 7: Commit**

```bash
git add src/usb_floppy_pi/gadget/sysfs_backend.py src/usb_floppy_pi/gadget/backend.py tests/unit/test_sysfs_backend.py
git commit -m "feat(gadget): SysfsBackend for Phase 2 kernel module"
```

---

## Task 17: Auto-detect backend in `__main__.py` + extend Config

Pick the right backend based on what's available; persist new settings.

**Files:**
- Modify: `src/usb_floppy_pi/__main__.py`
- Modify: `src/usb_floppy_pi/core/config.py`
- Test: extend `tests/unit/test_config.py`

- [ ] **Step 1: Extend the failing test**

Append to `tests/unit/test_config.py`:

```python
def test_config_has_phase2_fields() -> None:
    from usb_floppy_pi.core.config import Config
    cfg = Config()
    assert cfg.speed_preset == "floppy-real"
    assert cfg.volume == 70
    assert cfg.mute is False
    assert cfg.buzzer_enabled is True


def test_load_phase2_fields_from_json(tmp_path):
    from usb_floppy_pi.core.config import load_config
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(
        '{"speed_preset": "floppy-fast", "volume": 30, "mute": true, "buzzer_enabled": false}'
    )
    cfg = load_config(cfg_path)
    assert cfg.speed_preset == "floppy-fast"
    assert cfg.volume == 30
    assert cfg.mute is True
    assert cfg.buzzer_enabled is False
```

```bash
.venv/Scripts/python -m pytest tests/unit/test_config.py::test_config_has_phase2_fields tests/unit/test_config.py::test_load_phase2_fields_from_json -v
```

Expected: 2 failures (Config doesn't have these fields yet).

- [ ] **Step 2: Extend Config**

Edit `src/usb_floppy_pi/core/config.py`. Add fields to the dataclass:

```python
@dataclass
class Config:
    mute: bool = False
    buzzer_volume: float = 0.6   # Phase 1 — kept for backwards compat
    last_mounted: dict[str, str] | None = None
    samba_share_name: str = "floppies"
    log_level: str = "INFO"
    # Phase 2 additions
    speed_preset: str = "floppy-real"
    volume: int = 70
    buzzer_enabled: bool = True
```

(Note: `mute` already existed for the buzzer toggle. Reuse it.)

```bash
.venv/Scripts/python -m pytest tests/unit/test_config.py -v
```

Expected: 11 passed (9 existing + 2 new).

- [ ] **Step 3: Update `__main__.py` for backend auto-detect**

Edit `src/usb_floppy_pi/__main__.py`. Add `from .gadget.sysfs_backend import SysfsBackend`. Add a helper:

```python
def _auto_select_backend() -> GadgetBackend:
    """Pick the best available backend. Phase 2 kernel module preferred."""
    override = os.environ.get("USB_FLOPPY_BACKEND", "").strip().lower()
    if override == "sysfs":
        return SysfsBackend()
    if override == "configfs":
        return ConfigFsBackend()
    if override == "mock":
        from .gadget.backend import MockBackend
        return MockBackend()

    if Path("/sys/class/usb_floppy").exists():
        logger.info("Phase 2 kernel module detected → SysfsBackend")
        return SysfsBackend()
    if Path("/sys/kernel/config").exists():
        logger.info("Phase 1 configfs detected → ConfigFsBackend (legacy)")
        return ConfigFsBackend()
    raise RuntimeError(
        "Neither /sys/class/usb_floppy nor /sys/kernel/config available. "
        "Is the kernel module loaded?"
    )
```

In `_main_async`, replace the existing `backend = ConfigFsBackend()` with `backend = _auto_select_backend()`.

- [ ] **Step 4: Apply Phase 2 settings from config to backend at startup**

In `build_runtime`, after `controller.activate()` and before the wrapping of `mount`/`eject`, add:

```python
# Phase 2: apply config settings to the backend (no-op for Phase 1 backends).
backend = gadget_backend  # already in scope
backend.set_speed_preset(cfg.speed_preset)
backend.set_volume(cfg.volume)
backend.set_mute(cfg.mute)
backend.set_buzzer_enabled(cfg.buzzer_enabled)
```

These all default to `pass` on the Phase 1 backends so they're safe no-ops there.

- [ ] **Step 5: Run full suite**

```bash
.venv/Scripts/python -m pytest -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/usb_floppy_pi/core/config.py src/usb_floppy_pi/__main__.py tests/unit/test_config.py
git commit -m "feat: auto-select SysfsBackend (Phase 2) vs ConfigFsBackend (Phase 1) + Phase 2 config fields"
```

---

## Task 18: Web API endpoints for speed / volume / mute / buzzer

Add HTTP control points for the new kernel features.

**Files:**
- Modify: `src/usb_floppy_pi/web/api.py`
- Test: extend `tests/unit/test_web_api.py`

- [ ] **Step 1: Extend the test**

Append to `tests/unit/test_web_api.py`:

```python
def test_post_speed_preset(app_with_data) -> None:
    app, _, controller, _ = app_with_data
    # We need a backend that records speed_preset calls. Wrap MockBackend:
    class TrackedBackend:
        def __init__(self, inner): self._inner = inner; self.speed_preset = None
        def __getattr__(self, n): return getattr(self._inner, n)
        def set_speed_preset(self, p): self.speed_preset = p

    # The fixture's controller already has a MockBackend; we'll proxy through.
    # Instead of swapping, rely on the backend's default no-op for MockBackend
    # and just verify the endpoint returns 200 + correct body.
    with TestClient(app) as client:
        r = client.post("/api/speed", json={"preset": "floppy-fast"})
        assert r.status_code == 200
        assert r.json()["preset"] == "floppy-fast"


def test_post_volume(app_with_data) -> None:
    app, _, _, _ = app_with_data
    with TestClient(app) as client:
        r = client.post("/api/volume", json={"volume": 45})
        assert r.status_code == 200
        assert r.json()["volume"] == 45


def test_post_volume_out_of_range(app_with_data) -> None:
    app, _, _, _ = app_with_data
    with TestClient(app) as client:
        r = client.post("/api/volume", json={"volume": 200})
        assert r.status_code == 400


def test_post_mute(app_with_data) -> None:
    app, _, _, _ = app_with_data
    with TestClient(app) as client:
        r = client.post("/api/mute", json={"mute": True})
        assert r.status_code == 200
        assert r.json()["mute"] is True


def test_post_buzzer(app_with_data) -> None:
    app, _, _, _ = app_with_data
    with TestClient(app) as client:
        r = client.post("/api/buzzer", json={"enabled": False})
        assert r.status_code == 200
        assert r.json()["enabled"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/Scripts/python -m pytest tests/unit/test_web_api.py::test_post_speed_preset -v
```

Expected: 404 (endpoint doesn't exist yet).

- [ ] **Step 3: Add the endpoints**

Edit `src/usb_floppy_pi/web/api.py`. Add Pydantic request models:

```python
class SpeedRequest(BaseModel):
    preset: str

class VolumeRequest(BaseModel):
    volume: int

class MuteRequest(BaseModel):
    mute: bool

class BuzzerRequest(BaseModel):
    enabled: bool
```

Inside `build_app`, after the existing endpoints, add:

```python
@app.post("/api/speed")
def post_speed(req: SpeedRequest) -> dict:
    if req.preset not in {"floppy-real", "floppy-fast", "unthrottled"}:
        raise HTTPException(status_code=400, detail=f"unknown preset: {req.preset}")
    controller.backend.set_speed_preset(req.preset)
    return {"preset": req.preset}

@app.post("/api/volume")
def post_volume(req: VolumeRequest) -> dict:
    if req.volume < 0 or req.volume > 100:
        raise HTTPException(status_code=400, detail="volume must be 0..100")
    controller.backend.set_volume(req.volume)
    return {"volume": req.volume}

@app.post("/api/mute")
def post_mute(req: MuteRequest) -> dict:
    controller.backend.set_mute(req.mute)
    return {"mute": req.mute}

@app.post("/api/buzzer")
def post_buzzer(req: BuzzerRequest) -> dict:
    controller.backend.set_buzzer_enabled(req.enabled)
    return {"enabled": req.enabled}
```

- [ ] **Step 4: Expose `controller.backend`**

The endpoint above accesses `controller.backend`. Ensure `GadgetController` exposes the backend. Edit `src/usb_floppy_pi/gadget/controller.py`:

Find the `__init__` and add a property:

```python
@property
def backend(self) -> GadgetBackend:
    return self._backend
```

- [ ] **Step 5: Run tests**

```bash
.venv/Scripts/python -m pytest tests/unit/test_web_api.py -v
```

Expected: all pass (existing + 5 new).

- [ ] **Step 6: Run full suite + lint**

```bash
.venv/Scripts/python -m pytest -v
.venv/Scripts/python -m ruff check src tests
```

- [ ] **Step 7: Commit**

```bash
git add src/usb_floppy_pi/web/api.py src/usb_floppy_pi/gadget/controller.py tests/unit/test_web_api.py
git commit -m "feat(web): /api/{speed,volume,mute,buzzer} endpoints for Phase 2 controls"
```

---

## Task 19: Web UI controls for new endpoints

Add the visible controls in the browser.

**Files:**
- Modify: `src/usb_floppy_pi/web/static/index.html`
- Modify: `src/usb_floppy_pi/web/static/app.js`

- [ ] **Step 1: Add the HTML controls**

Edit `src/usb_floppy_pi/web/static/index.html`. After the upload form and before `</body>`, add:

```html
<div class="upload-form" id="phase2-controls" style="display:none">
    <h3 style="margin-top: 0">Hardware controls</h3>
    <div class="upload-row">
        <label for="speed-preset">Speed:</label>
        <select id="speed-preset">
            <option value="floppy-real">Real floppy (50 KB/s)</option>
            <option value="floppy-fast">Fast (200 KB/s)</option>
            <option value="unthrottled">Unthrottled</option>
        </select>
    </div>
    <div class="upload-row">
        <label for="volume">Volume:</label>
        <input type="range" id="volume" min="0" max="100" value="70">
        <span id="volume-display">70</span>
    </div>
    <div class="upload-row">
        <label>Buzzer:</label>
        <button id="buzzer-toggle">On</button>
        <button id="mute-toggle">Unmuted</button>
    </div>
</div>
```

- [ ] **Step 2: Add the JS handlers**

Append to `src/usb_floppy_pi/web/static/app.js`:

```javascript
// Phase 2: detect kernel module presence via /api/state extra fields.
async function refreshPhase2() {
    let state;
    try {
        state = await fetchJson("/api/state");
    } catch (e) { return; }
    const panel = document.getElementById("phase2-controls");
    if (!panel) return;
    if (state.metrics) {
        panel.style.display = "";
        const speed = document.getElementById("speed-preset");
        if (speed && state.metrics.speed_preset) speed.value = state.metrics.speed_preset;
        const vol = document.getElementById("volume");
        if (vol && typeof state.metrics.volume === "number") {
            vol.value = state.metrics.volume;
            document.getElementById("volume-display").textContent = state.metrics.volume;
        }
        const buzzer = document.getElementById("buzzer-toggle");
        if (buzzer) buzzer.textContent = state.metrics.buzzer ? "On" : "Off";
        const mute = document.getElementById("mute-toggle");
        if (mute) mute.textContent = state.metrics.mute ? "Muted" : "Unmuted";
    } else {
        panel.style.display = "none";
    }
}

document.getElementById("speed-preset")?.addEventListener("change", async (e) => {
    try {
        await fetchJson("/api/speed", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({preset: e.target.value}),
        });
    } catch (err) { alert("Speed change failed: " + err.message); }
});

document.getElementById("volume")?.addEventListener("input", (e) => {
    document.getElementById("volume-display").textContent = e.target.value;
});
document.getElementById("volume")?.addEventListener("change", async (e) => {
    try {
        await fetchJson("/api/volume", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({volume: parseInt(e.target.value)}),
        });
    } catch (err) { alert("Volume change failed: " + err.message); }
});

document.getElementById("buzzer-toggle")?.addEventListener("click", async () => {
    const cur = document.getElementById("buzzer-toggle").textContent === "On";
    try {
        await fetchJson("/api/buzzer", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({enabled: !cur}),
        });
        await refreshPhase2();
    } catch (err) { alert("Buzzer toggle failed: " + err.message); }
});

document.getElementById("mute-toggle")?.addEventListener("click", async () => {
    const cur = document.getElementById("mute-toggle").textContent === "Muted";
    try {
        await fetchJson("/api/mute", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({mute: !cur}),
        });
        await refreshPhase2();
    } catch (err) { alert("Mute toggle failed: " + err.message); }
});

// Hook into the existing periodic refresh.
const origRefresh = refresh;
refresh = async function() {
    await origRefresh();
    await refreshPhase2();
};
```

- [ ] **Step 3: Update `/api/state` to include metrics**

Edit `src/usb_floppy_pi/web/api.py`. Update the `get_state` endpoint:

```python
@app.get("/api/state")
def get_state() -> dict:
    m = controller.current
    metrics = {}
    try:
        metrics = controller.backend.get_metrics()
    except Exception:
        pass
    return {
        "mounted": (asdict(m) | {"backing_path": str(m.backing_path)}) if m else None,
        "metrics": metrics if metrics else None,
    }
```

- [ ] **Step 4: Run full suite**

```bash
.venv/Scripts/python -m pytest -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/usb_floppy_pi/web/static/index.html src/usb_floppy_pi/web/static/app.js src/usb_floppy_pi/web/api.py
git commit -m "feat(web): UI controls for speed preset, volume, buzzer, mute"
```

---

## Task 20: Modules-load + modprobe deploy configs

Configure the kernel module to load at boot with the right params.

**Files:**
- Create: `deploy/modules-load/usb-floppy-pi.conf`
- Create: `deploy/modprobe/usb-floppy-pi.conf`

- [ ] **Step 1: Write `deploy/modules-load/usb-floppy-pi.conf`**

```
g_floppy
```

- [ ] **Step 2: Write `deploy/modprobe/usb-floppy-pi.conf`**

```
options g_floppy file=/var/lib/usb-floppy-pi/current.img stall=0 removable=1 speed_preset=floppy-real volume=70 buzzer=1 subclass=ufi
```

- [ ] **Step 3: Commit**

```bash
git add deploy/modules-load/usb-floppy-pi.conf deploy/modprobe/usb-floppy-pi.conf
git commit -m "deploy: modules-load + modprobe configs for boot-time g_floppy with default params"
```

---

## Task 21: Update `install.sh` for DKMS + Phase 1 → Phase 2 migration

Make `sudo ./deploy/install.sh` a clean Phase 2 install on a fresh Pi, and a clean migration from a Phase 1 install.

**Files:**
- Modify: `deploy/install.sh`
- Modify: `deploy/boot/cmdline.txt.append` — drop `,libcomposite`
- Modify: `deploy/systemd/usb-floppy-pi.service` — drop libcomposite ExecStartPre

- [ ] **Step 1: Drop `libcomposite` from cmdline patch**

Edit `deploy/boot/cmdline.txt.append`:

```
modules-load=dwc2
```

(Remove the `,libcomposite`.)

- [ ] **Step 2: Drop libcomposite ExecStartPre from systemd unit**

Edit `deploy/systemd/usb-floppy-pi.service`. Remove the lines:
```
ExecStartPre=/sbin/modprobe libcomposite
ExecStartPre=/bin/sh -c 'mountpoint -q /sys/kernel/config || mount -t configfs none /sys/kernel/config'
```

(Keep everything else.)

- [ ] **Step 3: Add DKMS install + new configs to install.sh**

Edit `deploy/install.sh`. After the existing apt-install line, add:

```bash
echo "==> Installing DKMS + kernel headers"
apt-get install -y dkms raspberrypi-kernel-headers
```

After the "Copy / sync code to /opt" section, add:

```bash
# === Phase 2: install kernel module via DKMS ===
echo "==> Setting up g_floppy DKMS module"
mkdir -p /var/lib/usb-floppy-pi    # for current.img symlink

# If a previous version is registered, remove it to allow re-registration
if dkms status -m g-floppy -v 0.1.0 2>/dev/null | grep -q .; then
    dkms remove -m g-floppy -v 0.1.0 --all 2>/dev/null || true
fi
rm -rf /usr/src/g-floppy-0.1.0
cp -r "$INSTALL_DIR/kernel" /usr/src/g-floppy-0.1.0
dkms add -m g-floppy -v 0.1.0
dkms build -m g-floppy -v 0.1.0
dkms install -m g-floppy -v 0.1.0

# Install modules-load and modprobe configs
cp "$INSTALL_DIR/deploy/modules-load/usb-floppy-pi.conf" /etc/modules-load.d/
cp "$INSTALL_DIR/deploy/modprobe/usb-floppy-pi.conf" /etc/modprobe.d/

# Create initial current.img: empty file, gets pointed at the real .img by Python
if [[ ! -e /var/lib/usb-floppy-pi/current.img ]]; then
    dd if=/dev/zero of=/var/lib/usb-floppy-pi/current.img bs=1k count=1440
fi

# === Phase 1 cleanup: if there's a Phase 1 install, deactivate it cleanly ===
echo "==> Cleaning up Phase 1 configfs gadget if present"
systemctl stop usb-floppy-pi 2>/dev/null || true
echo "" > /sys/kernel/config/usb_gadget/floppy/UDC 2>/dev/null || true
rmmod g_mass_storage 2>/dev/null || true
# Drop libcomposite from cmdline if Phase 1 added it
sed -i 's/,libcomposite//g' "$BOOT_FW/cmdline.txt"
```

After the "Patching config.txt" block, ensure the PWM overlay line is added:

```bash
if ! grep -q "dtoverlay=pwm,pin=18" "$BOOT_FW/config.txt"; then
    sed -i '/=== usb-floppy-pi additions ===/a dtoverlay=pwm,pin=18,func=2' "$BOOT_FW/config.txt"
    echo "    PWM overlay added to config.txt"
fi
```

- [ ] **Step 4: Test the install path on the Pi**

(This is a destructive operation — the install.sh will rebuild everything. Ensure we have backups.)

```bash
python -c "
import paramiko, glob
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect('floppyusb', username='pi', password='floppy', look_for_keys=False, allow_agent=False)
sftp = c.open_sftp()
# Sync the entire repo. For brevity, we just sync deploy/ and kernel/ which are what install.sh touches:
import os
for root, dirs, files in os.walk('deploy'):
    for f in files:
        loc = os.path.join(root, f)
        rem = '/home/pi/Projects/usb-floppy-pi/' + loc.replace(chr(92), '/')
        # ensure remote dir exists
        try: sftp.stat(os.path.dirname(rem))
        except: 
            # attempt mkdir -p via separate ssh
            c.exec_command('mkdir -p ' + os.path.dirname(rem))
        sftp.put(loc, rem)
        print('uploaded', loc)
sftp.close(); c.close()
"

python .pi-dev-helper.py sudo "cd /home/pi/Projects/usb-floppy-pi && bash deploy/install.sh 2>&1 | tail -30"
```

Expected: install completes. After:

```bash
python .pi-dev-helper.py sudo "reboot"
```

Wait ~30s, then verify:

```bash
python .pi-dev-helper.py run "lsmod | grep g_floppy"
python .pi-dev-helper.py run "ls /sys/class/usb_floppy/usb-floppy-pi/"
python .pi-dev-helper.py sudo "systemctl status usb-floppy-pi --no-pager | head -10"
```

Expected: g_floppy in lsmod, sysfs class exists, service is active.

- [ ] **Step 5: Commit**

```bash
git add deploy/install.sh deploy/boot/cmdline.txt.append deploy/systemd/usb-floppy-pi.service
git commit -m "deploy(install): integrate DKMS install, Phase 1 cleanup, PWM overlay"
```

---

## Phase 2.4 — Buzzer audio: implementation log (2026-05-13)

Phase 2.4 was originally specced (Tasks 11-14 above) as a kernel-side
HW PWM buzzer that synthesises sound from `do_read`/`do_write` inside
`f_floppy.c`. We had to abandon that approach mid-execution.

**The blocker:** `pwm_request(int, const char *)` was removed in kernel
6.x in favour of the device-tree-bound `pwm_get(struct device *, const
char *)`. A USB gadget function driver has no own device tree node, so
neither API works for us — we'd have to register a fake platform device
solely to acquire a PWM consumer, which is fragile and version-coupled.

**What we did instead:** kept Task 11 (the `dtoverlay=pwm,pin=18,func=2`
overlay), threw out Tasks 12-14, and rebuilt Phase 2.4 as a userspace
audio stack driven by FlashFloppy-style track-step semantics:

- **Kernel**: `kernel/floppy_io_events.{c,h}` — atomic counters fed from
  `do_read`/`do_write`. A `track_crossings` counter increments by exactly
  the number of 36-sector boundaries each request crosses (computed from
  `lba + nblocks`, so it sees crossings *inside* a single multi-sector
  request too). Exposed as 5 RO sysfs attrs on
  `/sys/class/usb_floppy/usb-floppy-pi/`.
- **Userspace** (`src/usb_floppy_pi/audio/`):
  - `SysfsPWMBuzzer` drives `/sys/class/pwm/pwmchip0/pwm0/` (period/
    duty_cycle/enable), with `volume`/`mute`/`enabled` gating.
  - `SysfsIOEventReader` reads the kernel counters into an `IOEvent`
    snapshot.
  - `FloppyStepDetector` queues one pending click per
    `track_crossings` delta, drains at most one per tick (50 Hz),
    subject to a 2.7 ms mask matching FlashFloppy's minimum step cycle
    (`src/gotek/speaker.c`).
  - `SoundRenderer` plays each click as a 0.5 ms pulse at 2 kHz
    (≈one cycle at the EMAKERS piezo's resonance) — pure transient,
    not tonal. Long seeks render as "chunka-chunka-chunka".
- **Web API**: `/api/{volume,mute,buzzer}` hot-reload the live buzzer
  and persist to `config.json`. No restart needed.

**Hardware**: passive piezo buzzer module (with built-in transistor
buffer, sold as "Arduino passive buzzer module") wired to GPIO 18 +
5V + GND. We tried two active buzzers first that didn't work — see
the README for how to tell active vs passive at purchase time.

**Tuning history** (decisions worth preserving):
- The active buzzers we initially had (single 2300 Hz emission) made
  beep-beep only — couldn't follow PWM frequency changes.
- 1.2 kHz sustained tones for motor whir sounded synthy, not floppy-like.
- A continuous click stream sounded constant rather than mechanical.
- The breakthrough was reading FlashFloppy's `speaker.c`: clicks are
  *transient pulses* of a fraction of a millisecond, NOT tones. One
  pulse per real stepper step, silent between.
- LBA-only polling (early version) missed crossings inside multi-sector
  reads. Adding `track_crossings` kernel-side fixed it.

**Status**: ✅ shipped. 19 new tests (sysfs_pwm_buzzer 7, io_event_reader
2, state_machine 6, sound_renderer 5, audio_loop 2; plus 3 API
hot-reload + 4 wire-up in web). Full suite 133 passed. DKMS-installed
on Pi, survives reboot, auto-loads on boot.

---

## Phase 2.7 — Deployment validation log (2026-05-08)

End-to-end install validated on Pi Zero 2W (kernel `6.12.75+rpt-rpi-v8`):

- ✅ `install.sh` is idempotent — re-run after partial install / Phase 1
  cleanup completes without manual intervention
- ✅ DKMS install succeeds — modules persist as
  `/lib/modules/.../updates/dkms/{g_floppy,usb_f_floppy}.ko.xz`
- ✅ Boot-time auto-load via `/etc/modules-load.d/usb-floppy-pi.conf` —
  after `reboot`, `lsmod` shows `g_floppy`, `usb_f_floppy`, `libcomposite`
  with no manual `modprobe` needed
- ✅ Module parameters from `/etc/modprobe.d/usb-floppy-pi.conf` applied —
  `cat /sys/class/usb_floppy/usb-floppy-pi/speed_preset` → `floppy-real`
- ✅ `blank.img` + `current.img` symlink created — atomic retarget on
  mount/eject works
- ✅ `usb-floppy-pi.service` activates after reboot, restores last-mounted
  image, UDC reaches `configured`, host enumerates the floppy
- ✅ Web API `/api/state` returns full Phase 2 metrics (lun0_file, ro,
  speed_preset, speed_read_kbps, speed_write_kbps, seek_us)

**Deviations from the plan:**
- DKMS default `parallel_jobs=$(nproc)` (= 4 on Pi Zero 2W) **OOMs** the
  cc1 process during kernel-module compilation. Workaround added to
  `install.sh`: drops `/etc/dkms/framework.conf.d/usb-floppy-pi.conf` with
  `parallel_jobs=1`. Adds ~10 min to first build but unblocks the install.
- Phase 2.4 (HW PWM buzzer) is **deferred** — speed throttle covers the
  primary "feels like a real floppy" goal. The sysfs class still exposes
  `volume`/`mute`/`buzzer` placeholders for the web UI; their backing
  attributes are not currently created by the kernel module, and the API
  reports them as `null`.

## Task 22: Update README + final smoke test

Document the new state and verify the whole flow on real hardware.

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update README**

Replace `README.md` content with a Phase 2-aware version:

```markdown
# usb-floppy-pi

USB floppy drive emulator for Raspberry Pi Zero 2W. Connects to a retro PC via USB and presents itself as a 1.44 MB floppy drive (`A:` via BIOS legacy emulation, no host drivers required).

Designed for a 2010-era PC running DOS / Win98 SE dual-boot, but works as a generic USB floppy on any host.

## Status

✅ **Phase 1 (MVP headless):** USB Mass Storage gadget via configfs, web UI, Samba share, .img/.ima/.imz upload formats, last-mounted persistence, read-only and session mounts.

✅ **Phase 2 (kernel module):** Custom DKMS-packaged kernel module `g_floppy.ko`:
- UFI subclass — Windows always sees it as a floppy drive (independent of media presence)
- Configurable speed throttling: `floppy-real` (~50 KB/s, default), `floppy-fast`, `unthrottled`
- HW PWM buzzer with floppy emulation sounds (motor spin-up, head-load, track-seek clacks, spin-down)
- All controllable from the web UI (speed dropdown, volume slider, mute/buzzer toggles)

⏳ Future ideas: LCD + buttons (deferred), custom sound themes, multi-LUN.

## Hardware

- Raspberry Pi Zero 2W
- microSD ≥ 8 GB
- USB-A ↔ micro-USB **data** cable
- Passive piezo buzzer connected to GPIO 18 (BCM, header pin 12) and GND

## Install

1. Flash Raspberry Pi OS Lite (Bookworm 64-bit) with `rpi-imager`. Configure WiFi + SSH.
2. SSH into the Pi.
3. Clone this repo and run the installer:

```bash
git clone <repo-url> usb-floppy-pi
cd usb-floppy-pi
sudo ./deploy/install.sh
```

4. Reboot: `sudo reboot`

The installer:
- Installs `dkms`, kernel headers, samba, python deps
- Builds and installs the `g_floppy` kernel module via DKMS
- Configures `/etc/modules-load.d/` and `/etc/modprobe.d/` so the module loads at boot
- Adds `dtoverlay=pwm,pin=18,func=2` to `/boot/firmware/config.txt`
- Sets up Samba share `\\floppyusb\floppies`
- Enables the `usb-floppy-pi` systemd service for the web UI

## Use

- Connect Pi micro-USB **data** to the host PC USB.
- Power the Pi separately or via the same data cable (see spec for trade-offs).
- Web UI: `http://floppyusb.local` (or the Pi's IP).
- Samba share: `\\floppyusb\floppies` for drag-and-drop image management.
- Sounds: the buzzer should emit floppy-style sounds during host I/O. Volume/mute via the web UI.

## Layout

```
/home/pi/floppies/
├── DOS 6.22/
│   ├── ro                    ← read-only marker
│   ├── DISK001.img
│   └── DISK002.img
└── Win98 Boot/
    └── boot.img
```

## Development

Cross-platform unit tests (no Pi needed):

```bash
python -m venv .venv
source .venv/bin/activate          # or .venv\Scripts\activate on Windows
pip install -e ".[dev]"
pytest -v
```

The kernel module (`kernel/`) builds and tests on the Pi. See `kernel/README.md` for the dev workflow and `.pi-dev-helper.py` for SSH-based iteration.

## Spec & plans

- Spec Phase 1: `docs/superpowers/specs/2026-05-06-usb-floppy-pi-design.md`
- Spec Phase 2: `docs/superpowers/specs/2026-05-07-phase-2-kernel-module-design.md`
- Plan Phase 1: `docs/superpowers/plans/2026-05-06-phase-1-mvp-headless.md`
- Plan Phase 2: `docs/superpowers/plans/2026-05-07-phase-2-kernel-module.md`
```

- [ ] **Step 2: Run final smoke test on Pi**

This is a manual checklist:

- [ ] After fresh install + reboot, `lsmod | grep g_floppy` shows the module
- [ ] `/sys/class/usb_floppy/usb-floppy-pi/` exists with all expected attributes
- [ ] Web UI at `http://floppyusb.local` shows the Phase 2 controls (speed dropdown, volume slider, buzzer toggle, mute toggle)
- [ ] Mount an `.img` via web UI; verify `/sys/.../lun0_file` is updated
- [ ] Connect host PC; verify it enumerates as floppy
- [ ] Eject via web UI; verify Windows still shows the drive (UFI subclass works)
- [ ] Change speed preset to `unthrottled`; copy a file from host; verify it's fast
- [ ] Change speed preset to `floppy-real`; copy again; verify it's much slower
- [ ] With piezo connected, verify motor spin-up/clack/spin-down sounds during I/O
- [ ] Mute via web UI; verify silence
- [ ] Unmute; sound returns
- [ ] Adjust volume slider; sound level changes
- [ ] DKMS upgrade test: `sudo apt install --reinstall raspberrypi-kernel-headers && sudo reboot` (kernel may rebuild) — module should still load

- [ ] **Step 3: Commit + tag**

```bash
git add README.md
git commit -m "docs: README updated for Phase 2"

# Tag the milestone
git tag -a v0.2.0-phase2 -m "Phase 2 — kernel module with UFI, throttling, HW PWM buzzer"
```

---

## Self-Review (post-write)

**Spec coverage check** against `docs/superpowers/specs/2026-05-07-phase-2-kernel-module-design.md`:

| Spec section | Implemented in |
|--------------|---------------|
| §2.1 Reuso de código kernel via fork | Tasks 2, 3 |
| §2.2 Single-purpose gadget legacy | Tasks 4, 5 |
| §2.3 UFI subclass + module param fallback | Task 6 |
| §2.4 Speed throttling 3 presets | Tasks 9, 10 |
| §2.5 Buzzer HW PWM | Tasks 11, 12, 13, 14 |
| §2.6 Mapa de sonidos (sound table) | Task 13 |
| §2.7 Sin sonidos UI | (omission — verified, not implemented) |
| §3.1 Estructura del repo | Tasks 2-15 build the `kernel/` tree |
| §3.2-3.7 Submódulos + sysfs | Tasks 7-14 |
| §3.7 SysfsBackend | Task 16 |
| §3.8 Auto-detección | Task 17 |
| §4 Boot sequence | Implicit via Tasks 20, 21 |
| §5 DKMS packaging | Task 15 |
| §6 install.sh changes | Tasks 20, 21 |
| §7.1-7.7 Plan de fases internas | Mapped: 2.1=T4-5, 2.2=T6-8, 2.3=T9-10, 2.4=T11-14, 2.5=T15, 2.6=T16-19, 2.7=T20-22 |
| §8 Testing strategy | Throughout: Python TDD on Tasks 16-19, manual on kernel tasks |
| §9 Riesgos | Mitigations are part of the per-task verification |
| §10 Fuera de alcance | Honored (no LCD, no UI sounds, single LUN) |

**Placeholder scan:** Searched for "TBD", "TODO" (legitimate references in placeholder callbacks of Task 7 are intentional and replaced in Task 8). No phantom requirements.

**Type/method consistency check:**
- `floppy_throttle_state` struct fields match across `.h`, `.c`, and call sites
- `floppy_buzzer_state` struct fields match across `.h`, `.c`, and call sites
- `GadgetParams` constructor args used identically in Tasks 16, 17, and existing tests
- Sysfs attribute names: `lun0_file`, `lun0_ro`, `lun0_inquiry_string`, `speed_preset`, `speed_read_kbps`, `speed_write_kbps`, `seek_us`, `buzzer`, `mute`, `volume` — consistent across kernel attrs (Tasks 7, 8, 10, 14) and Python `SysfsBackend` (Task 16) and tests
- Module params: `file`, `ro`, `removable`, `stall`, `subclass`, `speed_preset`, `buzzer`, `volume`, `mute` — consistent in `f_floppy.c` (Task 6), `g_floppy_main.c` (Tasks 10, 14), and modprobe.d config (Task 20)

No inconsistencies found.

**Scope check:** This is one cohesive plan with ~22 tasks producing one deliverable (Phase 2 kernel module + Python integration). Estimated 13-17 days. Self-contained.
