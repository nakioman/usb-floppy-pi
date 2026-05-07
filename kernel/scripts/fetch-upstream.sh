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

cd "$(dirname "$0")/.."   # land in repo's kernel/ dir
echo "==> Copying as forks ..."
cp "$WORK/linux-${KVERSION}/drivers/usb/gadget/function/f_mass_storage.c"  ./f_floppy.c
cp "$WORK/linux-${KVERSION}/drivers/usb/gadget/function/f_mass_storage.h"  ./f_floppy.h
cp "$WORK/linux-${KVERSION}/drivers/usb/gadget/function/storage_common.c"  ./storage_common.c
cp "$WORK/linux-${KVERSION}/drivers/usb/gadget/function/storage_common.h"  ./storage_common.h
cp "$WORK/linux-${KVERSION}/drivers/usb/gadget/legacy/mass_storage.c"      ./g_floppy_main.c

echo "==> Done. Source files placed in kernel/."
echo "    Verify and commit. Source upstream: linux-${KVERSION}"
