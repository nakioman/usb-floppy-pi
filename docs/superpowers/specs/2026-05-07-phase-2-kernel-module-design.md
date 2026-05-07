# Phase 2 — Custom Kernel Module (`g_floppy.ko`)

**Fecha:** 2026-05-07
**Estado:** Aprobado para implementación
**Target hardware:** Raspberry Pi Zero 2W
**Dependencias:** Phase 1 (MVP headless) en producción

---

## 1. Visión y motivación

Phase 1 cumplió en lo básico: USB Mass Storage gadget, web UI, Samba, mount/eject. Pero quedaron tres limitaciones que solo se pueden resolver tocando el kernel:

1. **No es identificado como floppy "siempre"** — el módulo standard `f_mass_storage` usa SubClass=06 (SCSI). Cuando no hay media cargada, Windows demota al device a "USB drive genérico". Un floppy real (TEAC FD-05PUW) usa SubClass=04 (UFI), que hace que el OS lo trate como floppy independiente del media. El kernel hardcodea SubClass=06; no es configurable via configfs/sysfs.

2. **No hay control de velocidad de transferencia** — la transferencia corre a velocidad nativa USB 2.0 (~5 MB/s). No "se siente" como un floppy real (que era ~50 KB/s read). DOS y Win98 funcionan pero la experiencia retro pierde fidelidad.

3. **El sonido del floppy desde userspace es inviable con buena fidelidad** — para emitir clacks sincronizados con seeks reales de track, necesitamos LBA por request. `f_mass_storage` no expone eso. Un canal de eventos kernel→userspace agrega latencia, IPC y un proceso extra. Si tenemos que tocar el kernel igual para puntos 1 y 2, mejor poner el buzzer adentro.

**Phase 2 entrega:** un módulo de kernel `g_floppy.ko` (forkeado de `f_mass_storage` + `g_mass_storage` + `storage_common`) que:
- Declara SubClass=UFI → host trata como floppy real
- Tiene throttling configurable (presets nombrados, sysfs runtime)
- Maneja el buzzer internamente con HW PWM, reaccionando a I/O del host
- Se empaqueta como módulo DKMS para sobrevivir actualizaciones del kernel
- Es controlable desde la web UI Python existente vía un nuevo `SysfsBackend`

## 2. Decisiones de diseño

### 2.1 Reuso de código kernel

Forkeamos `f_mass_storage.c`, `storage_common.c`, `g_mass_storage.c` y headers correspondientes al directorio `kernel/` del repo. Lo mantenemos como módulo out-of-tree (DKMS), no patcheamos el kernel del Pi.

**Justificación:** `bInterfaceSubClass = USB_SC_SCSI` está hardcoded dentro de `f_mass_storage.c`; no hay forma de cambiarlo desde configfs ni vía module params. Reescribir el módulo desde cero implicaría reimplementar toda la SCSI command machinery (INQUIRY, READ_CAPACITY, READ_FORMAT_CAPACITIES, MODE SENSE, etc.) — semanas de trabajo y reinventar la rueda.

**Delta sobre upstream:** ~50 líneas de hooks añadidos a `f_floppy.c` (forkeado, ver §3.4 para detalle) + 3 archivos nuevos (`floppy_throttle.{c,h}`, `floppy_buzzer.{c,h}`, `g_floppy_main.c` ligeramente customizado). `storage_common.c/.h` se forkean sin modificaciones. Cuando salga un kernel con cambios relevantes upstream, se hace re-merge focalizado.

### 2.2 Single-purpose gadget legacy, no configfs

Phase 2 abandona configfs/libcomposite. El módulo es single-purpose (un único gadget mass-storage), cargable directamente con module params, igual que Pi-Floppy.

**Justificación:**
- Más simple de razonar (no hay árbol configfs externo, todo es state interno del módulo)
- Boot mucho más rápido — el módulo arranca el gadget en `modules-load.d`, antes de cualquier userspace
- DKMS-friendly (un solo `.ko`, sin scripts auxiliares de configfs)
- Coincide con Pi-Floppy y otros proyectos del ecosistema

### 2.3 UFI subclass

`bInterfaceSubClass = USB_SC_UFI` (0x04). El protocol queda `USB_PR_BULK` (0x50, BBB) — UFI usa CBI tradicionalmente pero todos los OS modernos aceptan UFI sobre BBB sin problemas.

**Beneficio confirmado:** Windows trata al device como floppy drive en Device Manager incluso sin media. Drive letter A: persiste sin cambiar. Para BIOSes retro como la H55, mejora la chance de ser asignado como USB-FDD legacy.

**Module param de fallback** `subclass=ufi|scsi` por si algún BIOS exótico no le gusta UFI; default = ufi.

### 2.4 Speed throttling

Tres presets nombrados:

| Preset | read_kbps | write_kbps | seek_us | Caso de uso |
|--------|-----------|------------|---------|-------------|
| `floppy-real` (default) | 50 | 30 | 6000 | HD floppy auténtico (1.44MB ≈ 30s read) |
| `floppy-fast` | 200 | 200 | 500 | Gotek Turbo, retro pero ágil |
| `unthrottled` | 0 | 0 | 0 | Bypass: ~5 MB/s native USB 2.0, debug |

`0` en cualquier campo = bypass de ese delay. Los presets son hardcoded en C; la API expone solo el nombre del preset (no los knobs individuales) para mantener UX simple.

Implementación: hooks en `do_read` / `do_write` de `f_floppy.c` llaman a `floppy_throttle_on_read/write` con `(lba, nblocks)`. La función:
1. Calcula `track = lba / 36` (CHS de 1.44MB: 36 sectors/track)
2. Si `track != last_track` y `seek_us > 0` → `usleep_range(seek_us, seek_us + 500)`
3. Calcula `io_us = nblocks * 512 * 1000 / read_kbps` y `usleep_range(io_us, io_us * 1.1)`

`usleep_range()` es bloqueante pero cede el CPU — corre en el contexto del kthread de FSG, no en interrupt handler.

### 2.5 Buzzer con HW PWM

GPIO 18 (PWM0). El módulo claimea el PWM channel via `pwm_get()` al cargar.

**Justificación HW PWM (vs SW PWM hrtimer):**
- Sample-perfect timing en chirps (rampas de frecuencia para spin-up/down) — sin escalonado audible
- 0% CPU durante tonos sostenidos
- Fidelidad uniforme en todo el rango de frecuencia (80Hz - 5kHz)

**Setup requerido:**
- `/boot/firmware/config.txt`: agregar `dtoverlay=pwm,pin=18,func=2`
- `dtparam=audio=off` ya estaba (sino choca con audio analógico)
- El módulo hace `pwm_get()` al init; si falla queda mute (degradación elegante, no rompe el USB gadget)

**Mapping volumen → duty cycle:** `volume` 0-100 → duty 0-50% (50% = max amplitude para piezo).

### 2.6 Mapa de sonidos

Cada secuencia es un array `const struct sound_step[]` compilado en el módulo:

```c
struct sound_step {
    u32 freq_hz;          // 0 = silencio
    u32 duration_us;
    u32 chirp_target_hz;  // 0 = tono fijo, !=0 = chirp lineal a este target
};
```

Eventos y secuencias:

| Evento | Detector | Secuencia |
|--------|----------|-----------|
| `motor_spin_up` | primer I/O tras ≥3s idle | chirp(200→500Hz, 600ms), luego entra a motor_loop |
| `head_load` | inmediatamente tras spin_up | clack: 150Hz × 60ms |
| `track_seek` | LBA cambia de track durante I/O activo | clack: 150Hz × 60ms |
| `multi_track_seek` | si Δtrack > 5 en una sola request | 3 clacks @ 80ms intervalo |
| `motor_loop` | mientras hay I/O activo | tono sostenido 400Hz, modulación ±5% cada 200ms |
| `motor_spin_down` | sin I/O por ≥3s | chirp(400→100Hz, 800ms) → silencio |
| `eject` | escritura a `lun0/file` de string vacío | spin_down + tick mecánico 50Hz × 100ms |

**State machine del motor:**
```
IDLE → I/O received → SPIN_UP → RUNNING
RUNNING → track change detected → emite clack (sigue RUNNING)
RUNNING → no I/O for 3s → SPIN_DOWN → IDLE
```

El sound engine corre en su propio kthread con hrtimer-driven scheduling. El hrtimer dispara al final de cada step; el callback aplica el siguiente step (cambia frecuencia del PWM o lo apaga) y reprograma. Para chirps, divide la rampa en ~30 micro-steps (cada ~20ms) para suavidad sample-perfect.

### 2.7 Sin sonidos de UI invocables desde userspace

No hay un sysfs `play_sound` ni equivalente. El módulo emite sonidos solo cuando hay actividad del host USB. Razones:
- "Es un floppy real" — un drive físico no toca melodías; solo gira y hace clacks cuando le piden.
- Mantiene la API minimal y enfocada
- Si alguna vez se necesitan UI sounds, se agrega en una phase posterior con un sysfs writeable de presets

## 3. Arquitectura

### 3.1 Estructura del repo

```
usb-floppy-pi/
├── kernel/                              # NUEVO en Phase 2
│   ├── Makefile                         # Kbuild out-of-tree
│   ├── dkms.conf                        # DKMS configuration
│   ├── README.md                        # build/install/debug del módulo
│   ├── f_floppy.c                       # FORK de f_mass_storage.c — modificado
│   ├── f_floppy.h                       # FORK de f_mass_storage.h
│   ├── storage_common.c                 # FORK as-is
│   ├── storage_common.h                 # FORK as-is
│   ├── g_floppy_main.c                  # FORK de g_mass_storage.c, módulo init/exit
│   ├── floppy_throttle.c                # NUEVO: rate-limiting de bulk transfers
│   ├── floppy_throttle.h
│   ├── floppy_buzzer.c                  # NUEVO: PWM driver + sound engine
│   └── floppy_buzzer.h
├── src/usb_floppy_pi/                   # Python existente (Phase 1)
│   ├── gadget/
│   │   ├── backend.py                   # Protocol existente, extendido con methods opcionales
│   │   ├── configfs_backend.py          # Phase 1 — queda como fallback
│   │   ├── sysfs_backend.py             # NUEVO Phase 2
│   │   └── controller.py                # sin cambios estructurales
│   ├── core/
│   │   └── config.py                    # extendido: speed_preset, volume, mute, buzzer
│   ├── web/
│   │   ├── api.py                       # endpoints nuevos: /api/speed, /api/volume, /api/buzzer
│   │   └── static/
│   │       ├── index.html               # UI extra: dropdown speed, slider volumen
│   │       └── app.js                   # handlers de los nuevos controles
│   └── __main__.py                      # auto-detect SysfsBackend vs ConfigFsBackend
├── deploy/
│   ├── install.sh                       # MODIFICADO: instala dkms + headers + módulo
│   ├── boot/
│   │   └── config.txt.append            # MODIFICADO: agrega dtoverlay=pwm
│   ├── modules-load/
│   │   └── usb-floppy-pi.conf           # NUEVO: g_floppy
│   ├── modprobe/
│   │   └── usb-floppy-pi.conf           # NUEVO: options g_floppy ...
│   └── systemd/
│       └── usb-floppy-pi.service        # MODIFICADO: ya no carga libcomposite
└── docs/superpowers/
    ├── specs/
    │   ├── 2026-05-06-usb-floppy-pi-design.md      # Phase 1
    │   └── 2026-05-07-phase-2-kernel-module-design.md   # ESTE DOCUMENTO
    └── plans/
        └── 2026-05-06-phase-1-mvp-headless.md
```

### 3.2 Submódulo `floppy_throttle`

Single source of truth para los presets y los hooks I/O.

```c
// floppy_throttle.h
struct floppy_throttle_state {
    u32 read_kbps;
    u32 write_kbps;
    u32 seek_us;
    u32 last_track;
    spinlock_t lock;
};

int  floppy_throttle_init(struct floppy_throttle_state *st);
void floppy_throttle_exit(struct floppy_throttle_state *st);
void floppy_throttle_on_read(struct floppy_throttle_state *st, u32 lba, u32 nblocks);
void floppy_throttle_on_write(struct floppy_throttle_state *st, u32 lba, u32 nblocks);
int  floppy_throttle_set_preset(struct floppy_throttle_state *st, const char *name);
ssize_t floppy_throttle_show_preset(struct floppy_throttle_state *st, char *buf);
```

### 3.3 Submódulo `floppy_buzzer`

Owns el PWM hardware, el sound engine kthread, el motor state machine.

```c
// floppy_buzzer.h
enum motor_state {
    MOTOR_IDLE,
    MOTOR_SPIN_UP,
    MOTOR_RUNNING,
    MOTOR_SPIN_DOWN,
};

struct floppy_buzzer_state {
    struct pwm_device *pwm;
    struct hrtimer scheduler;
    struct kthread_worker *worker;

    bool enabled;
    bool mute;
    u32 volume;          // 0..100

    enum motor_state state;
    ktime_t last_io;
    u32 last_track;

    const struct sound_step *active_seq;
    int active_pos;
    int active_len;

    spinlock_t lock;
};

int  floppy_buzzer_init(struct floppy_buzzer_state *st);
void floppy_buzzer_exit(struct floppy_buzzer_state *st);
void floppy_buzzer_on_io(struct floppy_buzzer_state *st, u32 lba, u32 nblocks, bool is_write);
void floppy_buzzer_on_eject(struct floppy_buzzer_state *st);

int  floppy_buzzer_set_mute(struct floppy_buzzer_state *st, bool mute);
int  floppy_buzzer_set_volume(struct floppy_buzzer_state *st, u32 volume);
int  floppy_buzzer_set_enabled(struct floppy_buzzer_state *st, bool enabled);
```

### 3.4 Modificaciones a `f_floppy.c`

Cambios sobre la copia upstream de `f_mass_storage.c`:

| Sección | Cambio |
|---------|--------|
| `fsg_intf_desc` | `bInterfaceSubClass = USB_SC_UFI` (línea aprox 150) |
| Includes top of file | `#include "floppy_throttle.h"` y `"floppy_buzzer.h"` |
| `do_read()` | después de parsear LBA y nblocks, antes de transferir: `floppy_throttle_on_read(...)` y `floppy_buzzer_on_io(..., false)` |
| `do_write()` | mismo patrón con `_on_write` y `is_write=true` |
| `eject` path (`fsg_store_file` para empty) | `floppy_buzzer_on_eject(...)` |

**Total LOC modificadas en `f_floppy.c`:** ~50 líneas. El resto del archivo (~3000 líneas) queda exactamente igual a upstream para minimizar deuda técnica de re-merge.

### 3.5 Submódulo `g_floppy_main` (módulo entry point)

`g_floppy_main.c` es el "driver entry": registra el USB gadget, parsea module params, inicializa los submódulos.

```c
// Module params
static char *file = "";
static char *speed_preset = "floppy-real";
static int buzzer = 1;
static int volume = 70;
static int mute = 0;
static char *subclass = "ufi";   // ufi | scsi (fallback)

module_param(file, charp, 0644);
module_param(speed_preset, charp, 0644);
module_param(buzzer, int, 0644);
module_param(volume, int, 0644);
module_param(mute, int, 0644);
module_param(subclass, charp, 0644);
```

`module_init()` orquesta:
1. Init `floppy_throttle_state` con preset elegido
2. Init `floppy_buzzer_state` (claimea PWM, lanza kthread)
3. Init FSG core con file=, ro=, etc.
4. Bind a primer UDC disponible
5. Crea sysfs class `/sys/class/usb_floppy/usb-floppy-pi/`

### 3.6 Sysfs interface

```
/sys/class/usb_floppy/usb-floppy-pi/
├── lun0/
│   ├── file              # rw — backing file path; "" o "\n" desmonta
│   ├── ro                # rw — 0|1 (rechaza cambio si hay file montado, igual que f_mass_storage)
│   ├── inquiry_string    # rw — SCSI INQUIRY string (28 chars)
│   ├── nofua             # rw — 0|1
│   ├── reads_total       # ro — counter
│   ├── writes_total      # ro — counter
│   └── current_track     # ro — última track accedida (debug)
├── speed_preset          # rw — "floppy-real" | "floppy-fast" | "unthrottled"
├── speed_read_kbps       # ro — derivado del preset
├── speed_write_kbps      # ro — derivado del preset
├── seek_us               # ro — derivado del preset
├── buzzer                # rw — 0|1, master enable
├── mute                  # rw — 0|1, mute temporal
├── volume                # rw — 0..100
└── motor_state           # ro — debug: idle|spin_up|running|spin_down
```

Lecturas devuelven el valor actual + newline. Escrituras parsean el valor y aplican (con validación). Los atributos `ro` no aceptan write.

### 3.7 Backend Python `SysfsBackend`

Implementa la `GadgetBackend` Protocol existente (`gadget/backend.py`). En lugar de escribir a `/sys/kernel/config/usb_gadget/...`, escribe a `/sys/class/usb_floppy/usb-floppy-pi/...`.

```python
# Métodos del Protocol — mismos que ConfigFsBackend
class SysfsBackend:
    SYSFS_ROOT = Path("/sys/class/usb_floppy/usb-floppy-pi")

    def create_gadget(self, params: GadgetParams) -> None:
        # No-op: el módulo creó el gadget al cargar.
        # Verificamos que el sysfs class existe; si no, raise.
        pass

    def destroy_gadget(self) -> None:
        # No-op: el módulo se descarga via rmmod si es necesario,
        # no via API runtime.
        pass

    def configure_lun(self, *, file: Path | None, ro: bool) -> None:
        # Mismo patrón que ConfigFsBackend pero a otra ruta:
        # write "\n" para clear, write path para attach.
        ...

    def attach_to_udc(self) -> None:
        # No-op: el módulo se atachó al UDC al cargar.
        pass

    def detach_from_udc(self) -> None:
        # No-op equivalent.
        pass

    # MÉTODOS NUEVOS (Phase 2)
    def set_speed_preset(self, preset: str) -> None: ...
    def set_volume(self, volume: int) -> None: ...
    def set_mute(self, mute: bool) -> None: ...
    def set_buzzer_enabled(self, enabled: bool) -> None: ...
    def get_metrics(self) -> dict: ...  # reads_total, writes_total, current_track
```

El `GadgetBackend` Protocol se extiende con estos métodos como **default implementations** que no hacen nada (`pass`). Así `ConfigFsBackend` (Phase 1) sigue cumpliendo el Protocol sin modificaciones.

### 3.8 Auto-detección en `__main__.py`

```python
def _build_backend() -> GadgetBackend:
    if Path("/sys/class/usb_floppy").exists():
        logger.info("Phase 2 kernel module detected — using SysfsBackend")
        return SysfsBackend()
    if Path("/sys/kernel/config").exists():
        logger.info("Phase 1 configfs detected — using ConfigFsBackend")
        return ConfigFsBackend()
    raise RuntimeError("neither /sys/class/usb_floppy nor configfs available — kernel modules not loaded?")
```

Override vía env var: `USB_FLOPPY_BACKEND=sysfs|configfs|auto`.

## 4. Boot sequence

```
[Pi power on]
[kernel boots ~3-5s]
  └─ /etc/modules-load.d/usb-floppy-pi.conf carga g_floppy con module params
        ├─ g_floppy_main: parsea params
        ├─ floppy_buzzer_init: pwm_request(), arranca kthread, hrtimer
        ├─ floppy_throttle_init: aplica preset
        ├─ FSG core init: configura SCSI/USB, abre file=
        ├─ Bind a UDC: gadget visible al host inmediatamente
        └─ sysfs class registration: /sys/class/usb_floppy/usb-floppy-pi/

[~3-5s después de power-on, host BIOS ya ve "TEAC FD-05PUW USB Floppy"]

[systemd llega a multi-user.target]
[~10-15s después, según optimizaciones de Phase 1]
  └─ usb-floppy-pi.service arranca
        ├─ load_config(/etc/usb-floppy-pi/config.json)
        ├─ Library scan + inotify
        ├─ Build SysfsBackend → GadgetController
        ├─ Si cfg.last_mounted válido → controller.mount(...) (escribe a /sys/.../lun0/file)
        ├─ Aplica cfg.speed_preset, cfg.volume, cfg.mute al backend
        └─ Uvicorn binds 0.0.0.0:80 → web UI online
```

**Diferencia clave vs Phase 1:** el USB gadget está bound al UDC **antes** de que systemd llegue a multi-user.target. La latencia hasta el host se vuelve función casi exclusiva del kernel boot, no de Python startup.

## 5. DKMS packaging

### 5.1 `dkms.conf`

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

### 5.2 Flujo de install

```bash
sudo apt install -y dkms raspberrypi-kernel-headers
sudo mkdir -p /usr/src/g-floppy-0.1.0
sudo cp -r kernel/* /usr/src/g-floppy-0.1.0/
sudo dkms add -m g-floppy -v 0.1.0
sudo dkms build -m g-floppy -v 0.1.0
sudo dkms install -m g-floppy -v 0.1.0
```

DKMS deja el `.ko` en `/lib/modules/$(uname -r)/extra/g_floppy.ko` y corre `depmod`. Después `modprobe g_floppy` funciona como cualquier módulo del sistema.

### 5.3 Sobrevivencia a kernel updates

DKMS hookea en los apt postinst del kernel. Cuando se instala un kernel nuevo:
1. APT termina de instalar el package del kernel
2. Trigger DKMS dispara `dkms autoinstall`
3. DKMS rebuilda el módulo contra los headers del kernel nuevo
4. Si falla (ej. signature kernel changes que necesitan code update), DKMS deja el módulo como `built` para el kernel viejo y emite warning
5. Reboot al kernel nuevo → módulo `extra/g_floppy.ko` está disponible

Si el rebuild falla, el usuario puede:
- Bootear desde grub al kernel anterior (que tiene el módulo funcional)
- Resolver el issue de compilación
- `sudo dkms install -m g-floppy -v 0.1.0 -k <new-kernel>` manual

## 6. Cambios al `install.sh` y deploy

`install.sh` actualizado:

```bash
# Apt deps adicionales para Phase 2
apt-get install -y dkms raspberrypi-kernel-headers

# Copiar source del módulo a /usr/src/
mkdir -p /usr/src/g-floppy-0.1.0
cp -r "$INSTALL_DIR/kernel/"* /usr/src/g-floppy-0.1.0/

# DKMS add/build/install (idempotente: si ya existe, skip)
if ! dkms status -m g-floppy -v 0.1.0 | grep -q installed; then
    dkms add -m g-floppy -v 0.1.0 || true   # ya añadido OK
    dkms build -m g-floppy -v 0.1.0
    dkms install -m g-floppy -v 0.1.0
fi

# Modules-load + modprobe configs
cp "$INSTALL_DIR/deploy/modules-load/usb-floppy-pi.conf" /etc/modules-load.d/
cp "$INSTALL_DIR/deploy/modprobe/usb-floppy-pi.conf" /etc/modprobe.d/

# Boot config: agregar PWM overlay
if ! grep -q "dtoverlay=pwm" "$BOOT_FW/config.txt"; then
    sed -i '/=== usb-floppy-pi additions ===/a dtoverlay=pwm,pin=18,func=2' "$BOOT_FW/config.txt"
fi

# Phase 1 → Phase 2 migration: deshabilitar libcomposite si quedó en cmdline
sed -i 's/,libcomposite//g' "$BOOT_FW/cmdline.txt"
```

## 7. Plan de fases internas

Subdivisión recomendada del work — cada etapa entrega algo testeable end-to-end:

### Phase 2.1 — Module bootstrap (3-4 días)
*Objetivo: módulo `g_floppy.ko` que carga, expone gadget USB, host enumera. Sin features nuevas.*

- Forkear archivos del kernel a `kernel/`
- Renombrar a `g_floppy`, ajustar Kbuild/Makefile
- Resolver imports y dependencias (`#include` paths)
- Build con `make KDIR=...`
- Cargar via `insmod` con un file existente
- Verificar `dmesg`, conectar host, validar enumeración

**Entregable:** módulo equivalente a `g_mass_storage` con nombres nuevos.

### Phase 2.2 — UFI subclass + sysfs class (1-2 días)
*Objetivo: que Windows trate al device como Floppy SIEMPRE.*

- Cambiar `bInterfaceSubClass = USB_SC_UFI` en `f_floppy.c`
- Registrar `/sys/class/usb_floppy/usb-floppy-pi/` con atributos básicos (lun0/file, lun0/ro, lun0/inquiry_string)
- Validar Windows Device Manager: "Floppy Disk Drive" sin importar media
- Validar BIOS de la H55: boot de un disk bootable

**Entregable:** floppy real para todo OS, drive letter persiste.

### Phase 2.3 — Speed throttling (2 días)
*Objetivo: 3 presets funcionales con timing medible.*

- Implementar `floppy_throttle.c`
- Hookear en `do_read` / `do_write`
- Module param + sysfs para `speed_preset`
- Test: `time dd if=/dev/sdX of=/dev/null bs=512 count=2880` con cada preset

**Entregable:** velocidad configurable runtime, sensación retro auténtica.

### Phase 2.4 — Buzzer + sonidos floppy (3-4 días)
*Objetivo: módulo emite sonidos al PWM al hacer I/O.*

- DT overlay para PWM0
- Implementar `floppy_buzzer.c`: `pwm_request`, hrtimer scheduler, sound primitives
- Implementar las 7 secuencias de la tabla de §2.6
- Motor state machine
- Hooks desde `f_floppy.c`
- Test: piezo conectado a GPIO 18, copiar archivos, escuchar

**Entregable:** sonido funcional, gobernable vía sysfs (mute, volume, buzzer enable).

### Phase 2.5 — DKMS packaging (1 día)
*Objetivo: el módulo persiste a apt upgrade del kernel.*

- Escribir `dkms.conf`
- Validar `dkms add/build/install`
- Probar el flujo de actualización: simular `apt upgrade` de kernel + reboot
- Documentar troubleshooting (header missing, build fail)

**Entregable:** instalación robusta entre updates.

### Phase 2.6 — Python integration (2-3 días)
*Objetivo: web UI controla todo lo nuevo.*

- Implementar `SysfsBackend`
- Auto-detect en `__main__.py` (configfs vs sysfs)
- Endpoints: `POST /api/speed`, `/api/volume`, `/api/buzzer`
- UI: dropdown presets, slider volumen, toggle buzzer
- Persistir nuevos settings en `config.json`

**Entregable:** misma web UI con controles para todas las features de Phase 2.

### Phase 2.7 — install.sh integration + Phase 1 deprecation (1 día)
*Objetivo: instalación end-to-end limpia.*

- Modificar `install.sh` para detectar Phase 1 instalado y migrar limpio
- Apt deps: `dkms`, `raspberrypi-kernel-headers`
- README updates
- Decidir destino de `ConfigFsBackend` (mi recomendación: dejarlo como fallback durante 1 release)

**Entregable:** clone repo + sudo install.sh = todo Phase 2 funcional.

**Estimación total:** 13-17 días de trabajo (con bug fixes y kernel module learning curve).

## 8. Testing strategy

**Tests Python existentes (78):** siguen funcionando. El refactor a `SysfsBackend` mantiene el mismo Protocol. Los 78 tests pasan después de cada cambio en Python.

**Tests de integración del módulo:** un nuevo `tests/integration/test_kernel_module.py` que se conecta vía SSH al Pi (paramiko) y verifica:
- Módulo carga (`lsmod | grep g_floppy`)
- sysfs class existe (`ls /sys/class/usb_floppy/`)
- Escribir un file path a `lun0/file` actualiza el atributo
- Leer counters después de I/O del host
- Cambiar `speed_preset` y verificar que `speed_read_kbps` se actualiza

Solo corre con flag `--run-kernel-tests`. CI no lo ejecuta (no hay Pi en CI); se corre localmente desde el dev machine.

**Smoke test manual:** un script `scripts/smoke-test-kernel-module.sh` que el desarrollador corre vía SSH al final de cada milestone:
```bash
# Cargar módulo, verificar /sys, conectar host, mount/eject, validar sonido
```

## 9. Riesgos conocidos y mitigaciones

| Riesgo | Probabilidad | Mitigación |
|--------|--------------|-----------|
| Kernel oops por bug en throttle/buzzer | Media | Defensive coding, no GFP_ATOMIC en fast path, kernel debug build con KASAN para tests, lots of pr_debug |
| `pwm_request()` falla porque otro driver claimea PWM0 | Baja | Module carga pero buzzer queda mute, log claro en dmesg explicando |
| DKMS rebuild falla en kernel update | Media | DKMS mantiene la versión vieja para el kernel anterior, fallback a ese kernel desde grub |
| `usleep_range` agrega más jitter del que queremos | Baja | Si pasa, switch a `hrtimer_nanosleep` para precision µs |
| Forks de kernel files divergen de upstream | Alta (a largo plazo) | Header comment en cada archivo: "FORKED FROM Linux 6.12, sync periodically", `kernel/UPSTREAM-DIFF.md` enumerando los cambios |
| Algún BIOS retro NO reconoce UFI subclass | Baja | Module param `subclass=ufi\|scsi`, default ufi, fallback fácil |
| El delay de `pwm_apply_state` por step de chirp es perceptible | Baja | Si pasa, pre-calcular array de períodos y solo aplicar (no recalcular) |

## 10. Fuera de alcance (deferred)

- **Multi-LUN** (sigue siendo 1 floppy)
- **Capacidades distintas a 1.44MB** (solo HD)
- **LCD + botones** (Phase 3 si se revive)
- **Sonidos de UI invocables desde fuera** (no, por filosofía "es un floppy real")
- **Audio sampleado vía I2S/DAC** (solo piezo PWM)
- **Buzzer en GPIO configurable** (hardcoded GPIO 18 / PWM0)
- **Soporte para placas no-RPi** (DKMS funciona en cualquier ARM Linux pero no validado)
- **Custom sound themes** (sound table hardcoded en C)
- **HID floppy controller** (algunos USB floppies declaran HID; no agrega nada para nuestro caso)
