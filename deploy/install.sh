#!/usr/bin/env bash
# USB Floppy Pi installer for Raspberry Pi OS Lite (Bookworm).
# Run as root on the Pi. Idempotent.
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

echo "==> usb-floppy-pi installer"
echo "    repo : $REPO_DIR"
echo "    target: $INSTALL_DIR"
echo

# === I2C level warning (LCD) — only relevant once Phase 2 is added, but flag now ===
echo "NOTE: When you add an LCD1602 backpack (Phase 2), the PCF8574 I2C pull-ups"
echo "      go to 5V while the Pi GPIO is 3V3 (not 5V tolerant). See spec §5.2."
echo

# === Apt packages ===
echo "==> Installing apt packages"
apt-get update
apt-get install -y python3 python3-venv python3-pip samba smbclient git

# === Copy / sync code to /opt ===
echo "==> Syncing code to $INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
rsync -a --delete \
    --exclude='.git' --exclude='.venv' --exclude='__pycache__' --exclude='.pytest_cache' \
    "$REPO_DIR/" "$INSTALL_DIR/"

# === Python venv ===
echo "==> Creating venv and installing"
if [[ ! -d $INSTALL_DIR/.venv ]]; then
    python3 -m venv "$INSTALL_DIR/.venv"
fi
"$INSTALL_DIR/.venv/bin/pip" install --upgrade pip
"$INSTALL_DIR/.venv/bin/pip" install -e "$INSTALL_DIR"

# === Default config ===
echo "==> Default config at $CONFIG_DIR"
mkdir -p "$CONFIG_DIR"
if [[ ! -f $CONFIG_DIR/config.json ]]; then
    cat > "$CONFIG_DIR/config.json" <<'JSON'
{
  "mute": false,
  "buzzer_volume": 0.6,
  "last_mounted": null,
  "samba_share_name": "floppies",
  "log_level": "INFO"
}
JSON
fi

# === Floppy root + ownership ===
mkdir -p "$FLOPPY_ROOT"
chown -R pi:pi "$FLOPPY_ROOT"

# === Samba ===
echo "==> Configuring Samba share"
SAMBA_USER="${SAMBA_USER:-floppy}"
SHARE_NAME="${SHARE_NAME:-floppies}"

# Render template by simple sed
cp "$INSTALL_DIR/deploy/samba/smb.conf.j2" /etc/samba/smb.conf
sed -i "s|{{ share_name }}|$SHARE_NAME|g" /etc/samba/smb.conf
sed -i "s|{{ samba_user }}|$SAMBA_USER|g" /etc/samba/smb.conf

# Create samba user (and supporting group) if missing. Idempotent on reinstall:
# a previous install may have left the group behind, in which case useradd
# without -g would fail trying to create a group of the same name.
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

# === Boot config ===
echo "==> Patching $BOOT_FW/config.txt and cmdline.txt"
if ! grep -q "usb-floppy-pi additions" "$BOOT_FW/config.txt"; then
    cat "$INSTALL_DIR/deploy/boot/config.txt.append" >> "$BOOT_FW/config.txt"
    echo "    config.txt patched"
else
    echo "    config.txt already patched"
fi

if ! grep -q "modules-load=dwc2" "$BOOT_FW/cmdline.txt"; then
    # cmdline.txt is a single line; append in-place
    sed -i 's|$| modules-load=dwc2,libcomposite|' "$BOOT_FW/cmdline.txt"
    echo "    cmdline.txt patched"
elif ! grep -q "libcomposite" "$BOOT_FW/cmdline.txt"; then
    # already has dwc2, but missing libcomposite — add it inline
    sed -i 's|modules-load=dwc2\b|modules-load=dwc2,libcomposite|' "$BOOT_FW/cmdline.txt"
    echo "    cmdline.txt updated to include libcomposite"
else
    echo "    cmdline.txt already patched"
fi

# Also ensure libcomposite is loaded via /etc/modules (fallback if cmdline fails)
if ! grep -qx "libcomposite" /etc/modules 2>/dev/null; then
    echo "libcomposite" >> /etc/modules
    echo "    libcomposite added to /etc/modules"
fi

# === systemd unit ===
echo "==> Installing systemd unit"
cp "$INSTALL_DIR/deploy/systemd/usb-floppy-pi.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable usb-floppy-pi.service

echo
echo "==> Installation complete."
echo "    Reboot for the dwc2 + cmdline changes to take effect:"
echo "      sudo reboot"
echo
echo "    After reboot:"
echo "      - Connect the Pi micro-USB-DATA port to the host PC"
echo "      - Visit http://floppy.local (or the Pi's IP) for the web UI"
echo "      - Mount the Samba share at \\\\floppy\\$SHARE_NAME"
