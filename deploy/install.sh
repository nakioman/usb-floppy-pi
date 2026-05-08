#!/usr/bin/env bash
# USB Floppy Pi installer for Raspberry Pi OS Lite (Bookworm).
# Run as root on the Pi. Idempotent — safe to re-run after a partial install
# or when upgrading from Phase 1 to Phase 2.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "ERROR: must be run as root (use sudo)" >&2
    exit 1
fi

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_DIR=/opt/usb-floppy-pi
CONFIG_DIR=/etc/usb-floppy-pi
FLOPPY_ROOT=/home/pi/floppies
BOOT_FW=/boot/firmware
VAR_DIR=/var/lib/usb-floppy-pi
DKMS_NAME=g-floppy
DKMS_VER=0.1.0

echo "==> usb-floppy-pi installer (Phase 2 — kernel module + DKMS)"
echo "    repo : $REPO_DIR"
echo "    target: $INSTALL_DIR"
echo

# === Apt packages ===
echo "==> Installing apt packages"
apt-get update
# Phase 1 deps + Phase 2 (dkms, kernel headers, dosfstools for blank.img mkfs).
apt-get install -y python3 python3-venv python3-pip samba smbclient git \
    dkms dosfstools linux-headers-rpi-v8 linux-kbuild-6.12.75+rpt 2>/dev/null || \
    apt-get install -y python3 python3-venv python3-pip samba smbclient git \
        dkms dosfstools "linux-headers-$(uname -r)"

# === Phase 1 → Phase 2 cleanup ============================================
# If a previous Phase 1 install is running with libcomposite/configfs, stop
# the service and tear down the gadget cleanly before swapping kernel modules.
echo "==> Cleaning up any running Phase 1 gadget"
systemctl stop usb-floppy-pi 2>/dev/null || true
if [[ -d /sys/kernel/config/usb_gadget/floppy ]]; then
    echo "" > /sys/kernel/config/usb_gadget/floppy/UDC 2>/dev/null || true
    cd /sys/kernel/config/usb_gadget/floppy
    rm -f configs/c.1/mass_storage.usb0
    rmdir configs/c.1/strings/0x409 2>/dev/null || true
    rmdir configs/c.1 2>/dev/null || true
    rmdir functions/mass_storage.usb0/lun.0 2>/dev/null || true
    rmdir functions/mass_storage.usb0 2>/dev/null || true
    rmdir strings/0x409 2>/dev/null || true
    cd /
    rmdir /sys/kernel/config/usb_gadget/floppy 2>/dev/null || true
fi
rmmod usb_f_mass_storage 2>/dev/null || true

# Phase 2 doesn't need libcomposite in cmdline (it's pulled by g_floppy via
# module dependencies). Strip it if Phase 1 left it there.
if grep -q "libcomposite" "$BOOT_FW/cmdline.txt" 2>/dev/null; then
    sed -i 's/,libcomposite//g' "$BOOT_FW/cmdline.txt"
    echo "    removed ',libcomposite' from cmdline.txt (no longer needed in Phase 2)"
fi
sed -i '/^libcomposite$/d' /etc/modules 2>/dev/null || true

# === Sync code to /opt =====================================================
echo "==> Syncing code to $INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
rsync -a --delete \
    --exclude='.git' --exclude='.venv' --exclude='__pycache__' \
    --exclude='.pytest_cache' --exclude='.pi-dev-helper.py' \
    "$REPO_DIR/" "$INSTALL_DIR/"

# === Python venv ============================================================
echo "==> Creating venv and installing Python deps"
if [[ ! -d $INSTALL_DIR/.venv ]]; then
    python3 -m venv "$INSTALL_DIR/.venv"
fi
"$INSTALL_DIR/.venv/bin/pip" install --upgrade pip >/dev/null
"$INSTALL_DIR/.venv/bin/pip" install -e "$INSTALL_DIR" >/dev/null

# === DKMS install of g_floppy + usb_f_floppy ===============================
echo "==> Installing g_floppy kernel module via DKMS"
mkdir -p "$VAR_DIR"

# Pi Zero 2W has 512MB RAM — kernel-module compilation OOMs with the default
# parallel_jobs=nproc (4 on this CPU). Force -j1 globally for DKMS so the
# build fits in memory. Idempotent: just re-writes the file each run.
mkdir -p /etc/dkms/framework.conf.d
cat > /etc/dkms/framework.conf.d/usb-floppy-pi.conf <<'EOF'
# Lower DKMS parallelism — Pi Zero 2W (512MB) OOMs on -j4 kernel-module builds.
parallel_jobs=1
EOF

# Re-register source dir from this checkout (idempotent: remove stale entry).
if dkms status -m "$DKMS_NAME" -v "$DKMS_VER" 2>/dev/null | grep -q .; then
    dkms remove -m "$DKMS_NAME" -v "$DKMS_VER" --all 2>/dev/null || true
fi
rm -rf "/usr/src/${DKMS_NAME}-${DKMS_VER}"
cp -r "$INSTALL_DIR/kernel" "/usr/src/${DKMS_NAME}-${DKMS_VER}"
dkms add    -m "$DKMS_NAME" -v "$DKMS_VER"
dkms build  -m "$DKMS_NAME" -v "$DKMS_VER"
dkms install -m "$DKMS_NAME" -v "$DKMS_VER"
echo "    DKMS installed g_floppy + usb_f_floppy for kernel $(uname -r)"

# === blank.img + current.img symlink =======================================
# blank.img is a 1.44MB FAT12 image with label BLANK. It guarantees the
# kernel always loads with a valid backing file at boot, so Windows
# classifies the device as Floppy on the first enumeration.
# current.img is a symlink Python updates whenever the user mounts/ejects.
echo "==> Setting up blank.img + current.img symlink"
if [[ ! -f $VAR_DIR/blank.img ]] || [[ $(stat -c%s "$VAR_DIR/blank.img") -ne 1474560 ]]; then
    dd if=/dev/zero of="$VAR_DIR/blank.img" bs=1024 count=1440 status=none
    mkfs.vfat -F 12 -n BLANK "$VAR_DIR/blank.img" >/dev/null
    echo "    created blank.img (1.44MB FAT12)"
fi
if [[ ! -L $VAR_DIR/current.img ]]; then
    rm -f "$VAR_DIR/current.img"
    ln -s blank.img "$VAR_DIR/current.img"
    echo "    current.img -> blank.img (initial)"
fi

# === modules-load + modprobe configs =======================================
echo "==> Installing modules-load.d + modprobe.d configs"
cp "$INSTALL_DIR/deploy/modules-load/usb-floppy-pi.conf" /etc/modules-load.d/
cp "$INSTALL_DIR/deploy/modprobe/usb-floppy-pi.conf" /etc/modprobe.d/

# === Default Python config =================================================
echo "==> Default config at $CONFIG_DIR"
mkdir -p "$CONFIG_DIR"
if [[ ! -f $CONFIG_DIR/config.json ]]; then
    cat > "$CONFIG_DIR/config.json" <<'JSON'
{
  "mute": false,
  "buzzer_volume": 0.6,
  "last_mounted": null,
  "samba_share_name": "floppies",
  "log_level": "INFO",
  "speed_preset": "floppy-real",
  "volume": 70,
  "buzzer_enabled": true
}
JSON
fi

# === Floppy root + ownership ===============================================
mkdir -p "$FLOPPY_ROOT"
chown -R pi:pi "$FLOPPY_ROOT"

# === Samba =================================================================
echo "==> Configuring Samba share"
SAMBA_USER="${SAMBA_USER:-floppy}"
SHARE_NAME="${SHARE_NAME:-floppies}"

cp "$INSTALL_DIR/deploy/samba/smb.conf.j2" /etc/samba/smb.conf
sed -i "s|{{ share_name }}|$SHARE_NAME|g" /etc/samba/smb.conf
sed -i "s|{{ samba_user }}|$SAMBA_USER|g" /etc/samba/smb.conf

# Idempotent user/group creation (re-run safe).
if ! getent group "$SAMBA_USER" >/dev/null; then
    groupadd --system "$SAMBA_USER"
fi
if ! id -u "$SAMBA_USER" >/dev/null 2>&1; then
    useradd --no-create-home --shell /usr/sbin/nologin \
        --gid "$SAMBA_USER" "$SAMBA_USER"
fi
if ! pdbedit -L | grep -q "^$SAMBA_USER:"; then
    echo "==> Set Samba password for user '$SAMBA_USER':"
    smbpasswd -a "$SAMBA_USER"
fi
systemctl restart smbd

# === Boot config (config.txt) ==============================================
echo "==> Patching $BOOT_FW/config.txt"
if ! grep -q "usb-floppy-pi additions" "$BOOT_FW/config.txt"; then
    cat "$INSTALL_DIR/deploy/boot/config.txt.append" >> "$BOOT_FW/config.txt"
    echo "    config.txt patched"
else
    echo "    config.txt already patched"
fi

# cmdline.txt: keep dwc2 (need it for OTG mode); libcomposite no longer needed.
if ! grep -q "modules-load=dwc2" "$BOOT_FW/cmdline.txt"; then
    sed -i 's|$| modules-load=dwc2|' "$BOOT_FW/cmdline.txt"
    echo "    cmdline.txt patched"
fi

# === systemd unit ==========================================================
echo "==> Installing systemd unit"
cp "$INSTALL_DIR/deploy/systemd/usb-floppy-pi.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable usb-floppy-pi.service

# === Try to load g_floppy now (so user sees it work without rebooting) =====
# If dwc2 is already loaded (it is on a system that previously had Phase 1),
# we can modprobe g_floppy right away.
if [[ -d /sys/class/udc ]] && ls /sys/class/udc/ 2>/dev/null | grep -q .; then
    echo "==> Loading g_floppy now"
    modprobe g_floppy 2>&1 || echo "    (modprobe failed; will load at next reboot)"
fi

# Start the Python service. It auto-detects the new kernel module via
# /sys/class/usb_floppy and uses SysfsBackend.
systemctl restart usb-floppy-pi 2>/dev/null || true

echo
echo "==> Installation complete."
echo
if ! cat /sys/class/udc/*/state 2>/dev/null | grep -q configured; then
    echo "    Reboot recommended (dwc2 + boot params may not be active yet):"
    echo "      sudo reboot"
    echo
fi
echo "    After connecting the Pi to the host:"
echo "      - Visit http://$(hostname).local (or the Pi's IP) for the web UI"
echo "      - Mount the Samba share at \\\\$(hostname)\\$SHARE_NAME"
echo
echo "    Phase 2 features:"
echo "      - Speed throttle: 'Real floppy / Fast / Unthrottled' selectable from web"
echo "      - Floppy identity: A: in Windows even with no media (via blank.img fallback)"
