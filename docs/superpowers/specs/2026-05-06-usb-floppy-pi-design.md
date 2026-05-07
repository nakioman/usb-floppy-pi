# USB Floppy Pi — Diseño

**Fecha:** 2026-05-06
**Estado:** Aprobado para implementación
**Target hardware:** Raspberry Pi Zero 2W
**Lenguaje:** Python 3.11+

---

## 1. Visión

Convertir una Raspberry Pi Zero 2W en un dispositivo **USB floppy emulado** que se conecta a un PC retro (Biostar H55 + i3 540, dual-boot DOS + Win98SE) y lo ve como una disquetera 1.44 MB tradicional vía la legacy emulation del BIOS (presentando el dispositivo como `A:` tanto en DOS como en Win98 sin drivers extra).

El dispositivo combina:
- Estética de "frente de floppy drive" con LCD 1602 + 2 botones + buzzer
- Control físico para navegar/cambiar imágenes sin necesidad de otro PC
- Web UI por WiFi para gestión cómoda desde móvil/PC moderno
- Compartido Samba para arrastrar imágenes desde cualquier máquina de la red
- Sonidos retro (motor, lectura, escritura, eject) que evocan un floppy real

## 2. Decisiones de diseño

### 2.1 Modo de operación

USB Mass Storage gadget (modo `g_mass_storage` vía `dwc2` + `libcomposite` / configfs). El RPi Zero 2W expone su puerto USB OTG como dispositivo USB conectado al PC retro. No emula señales Shugart/floppy (eso requeriría hardware extra y trabajo a nivel de timings).

### 2.2 Capacidad y formato

**Solo 1.44 MB (HD 3.5", 1474560 bytes).** Una sola capacidad fija simplifica el gadget USB (no hace falta re-enumerar al cambiar imagen). Imágenes más pequeñas se rellenan con ceros al montar; imágenes mayores son rechazadas con error.

**Formato canónico interno:** `.img` raw (FAT12 dentro). No soportamos `.adf`, `.hfe`, `.imd`, etc. — el host es un PC x86, no necesita formatos exóticos.

**Formatos aceptados en input** (Samba upload + web upload):

| Extensión | Tratamiento |
|-----------|-------------|
| `.img` | aceptar tal cual (formato canónico) |
| `.ima` (case-insensitive) | renombrar a `.img` (es byte-por-byte idéntico, solo cambia la extensión) |
| `.imz` (case-insensitive) | extraer contenido (es un ZIP con una imagen dentro) → guardar como `.img` → borrar el `.imz` original |

La normalización pasa al detectar el fichero (vía inotify para Samba, en el handler de upload para web). La biblioteca en memoria **siempre** contiene rutas `.img`.

**Reglas de normalización para `.imz`:**

- Si el ZIP contiene varios ficheros, se extrae **solo el primero válido** (extensión `.ima`/`.img`, tamaño ≤1.44MB tras descomprimir). Los demás se descartan con warning en el log.
- Si al extraer ya existe `<nombre>.img` en el destino, el extraído se renombra a `<nombre> (1).img`, `<nombre> (2).img`, etc. para no perder datos.
- Si el ZIP está corrupto o no contiene ninguna imagen válida, se deja el `.imz` en su sitio y se loguea un error visible en la web.

### 2.3 Modos de escritura

Por convención del filesystem (no en base de datos):

- **`rw` (default):** la imagen se modifica directamente. El gadget USB usa `O_SYNC` y `sync_file_range` periódico para mitigar corrupción por apagado abrupto.
- **`ro`:** un fichero de marker `ro` (cualquier contenido, basta con que exista) en la carpeta del set la marca como read-only. El gadget se crea con `ro=1`.
- **`session` (solo desde web):** opción puntual de mount que copia la imagen a `/tmp/floppy-session.img`, monta la copia, y la descarta al eject. No expuesta en LCD para mantener UI mínima.

### 2.4 Control de la "biblioteca" de imágenes

**El filesystem es el modelo de datos.** No hay base de datos.

```
/home/pi/floppies/                      ← compartido por Samba como "floppies"
├── DOS 6.22/
│   ├── ro                              ← marker = read-only
│   ├── DISK001.img
│   ├── DISK002.img
│   └── DISK003.img
├── Win98 Boot/
│   └── boot.img                        ← un solo disco, sin marker = writable
└── Quake Shareware/
    ├── ro
    ├── DISK1.img
    └── DISK2.img
```

**Reglas:**
- Cada subcarpeta de `/home/pi/floppies/` = un "set de disquetes"
- Nombre de la carpeta = display name del set
- Cada `.img` dentro = un disquete del set, ordenados alfabéticamente
- Estructura plana, **un solo nivel** de carpetas (sin nesting)
- Marker `ro` (cualquier contenido) → todo el set es read-only

**Hot-reload vía `inotify`:** copiar/borrar/modificar carpetas en `/home/pi/floppies/` por Samba refresca el menú al instante. El watcher también aplica la normalización de formatos (`.ima` → renombrar, `.imz` → extraer y borrar) descrita en 2.2 antes de añadir la imagen al estado.

### 2.5 Comportamiento al arrancar

Recordamos en `config.json` la última imagen montada y la re-montamos al boot (comportamiento de "floppy que se quedó dentro"). Si el fichero ya no existe (lo borraron por Samba mientras el Pi estaba apagado), arrancamos sin imagen montada.

### 2.6 Alimentación

**Exclusivamente desde el USB del PC retro** — un solo cable, estética limpia. Aceptamos el riesgo de corrupción por apagado abrupto, que mitigamos con sync agresivo. No usamos UPS, supercondensador ni alimentación externa en MVP.

### 2.7 Configuración de software

`/etc/usb-floppy-pi/config.json` (JSON nativo, sin DB):

```json
{
  "mute": false,
  "buzzer_volume": 0.6,
  "last_mounted": {
    "set": "DOS 6.22",
    "disk": "DISK001.img"
  },
  "samba_share_name": "floppies",
  "log_level": "INFO"
}
```

Escritura atómica: `tmp + fsync + rename`.

## 3. Arquitectura

### 3.1 Stack

| Capa | Tecnología | Razón |
|------|-----------|-------|
| OS | Raspberry Pi OS Lite (Bookworm 64-bit) | Soporte oficial Pi Zero 2W, kernel reciente con configfs gadget |
| Lenguaje | Python 3.11+ | Productividad, ecosistema RPi maduro |
| Async runtime | `asyncio` | Múltiples subsistemas concurrentes con I/O ligero |
| Web | FastAPI + uvicorn | Async-native, encaja en el mismo loop |
| GPIO | gpiozero (backend lgpio) | Default en Bookworm, API limpia |
| PWM buzzer | pigpio (`pigpiod` daemon) | PWM por hardware, tono estable |
| LCD | RPLCD.i2c | Driver maduro PCF8574 + HD44780 |
| Persistencia | JSON + filesystem | Simplicidad, debug trivial |
| Compartir red | Samba (servicio del sistema) | Estándar de facto, nada que escribir |
| USB gadget | configfs (`/sys/kernel/config/usb_gadget/`) | API estable del kernel, sin libs exóticas |

### 3.2 Proceso único `usb-floppy-pi` (systemd unit)

Un único event loop de asyncio con 8 módulos cooperando:

```
┌─────────────────────────────────────────────────────────────┐
│                    usb-floppy-pi (asyncio)                   │
│                                                              │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────────┐  │
│  │ web_api  │  │ buttons  │  │   lcd    │  │   audio    │  │
│  │ FastAPI  │  │  GPIO    │  │ renderer │  │  buzzer    │  │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └─────┬──────┘  │
│       │             │             │              │         │
│       ▼             ▼             ▼              ▼         │
│  ┌──────────────────────────────────────────────────────┐  │
│  │              core: state + event_bus                 │  │
│  │  (in-memory state, asyncio.Queue de eventos)         │  │
│  └────────┬─────────────────────────────┬───────────────┘  │
│           │                             │                  │
│           ▼                             ▼                  │
│  ┌──────────────┐                ┌──────────────┐         │
│  │ gadget_ctrl  │                │  storage     │         │
│  │  (configfs)  │                │ (fs + json)  │         │
│  └──────┬───────┘                └──────────────┘         │
│         ▼                                                  │
│  ┌──────────────┐                                          │
│  │  activity    │                                          │
│  │  monitor     │ ← detecta lectura/escritura del host     │
│  └──────────────┘                                          │
└─────────────────────────────────────────────────────────────┘
```

### 3.3 Responsabilidades por módulo

| Módulo | Responsabilidad | Entrada | Salida |
|--------|----------------|---------|--------|
| `core.state` | Estado en memoria (dataclass `AppState`: imagen actual, cursor de menú, mute, etc.) | mutaciones desde otros módulos | leído por todos |
| `core.event_bus` | `asyncio.Queue` de eventos (`button_press`, `host_read`, `image_changed`…) | publicado por hardware/gadget | consumido por audio/lcd |
| `core.config` | Carga/escribe `config.json` atómicamente | filesystem | settings dict |
| `web_api` | Web UI + REST API (subir, listar, mount/eject, mute) | HTTP | mutaciones a `state`, llamadas a `gadget_ctrl` |
| `hardware.buttons` | Polling GPIO (~20ms) + decodificación corto/largo/doble/dual-press | GPIO | eventos al bus |
| `hardware.lcd` | Renderiza pantalla actual (menú, info imagen, splash) | `state` + eventos | I2C |
| `hardware.audio` | Mapea eventos a tonos/loops del buzzer pasivo | bus + state.mute | PWM hw pin |
| `gadget.controller` | Crea/cambia/destruye el USB gadget (configfs) | API call (`mount(image)`, `eject()`) | `/sys/kernel/config` |
| `gadget.activity` | Detecta E/S del host (sysfs del LUN) | sysfs polling (~50ms) | eventos al bus |
| `storage.library` | Scanner del FS, watcher inotify de `/home/pi/floppies/` | filesystem | construye `list[FloppySet]` |
| `ui.menus` | Máquina de estados del menú LCD | eventos (botones) + state | mutaciones de `state.cursor_screen` |

### 3.4 Estado en memoria

```python
@dataclass
class FloppySet:
    name: str                          # "DOS 6.22"
    path: Path                         # /home/pi/floppies/DOS 6.22
    disks: list[Path]                  # [DISK001.img, DISK002.img, ...]
    read_only: bool                    # presencia de fichero "ro"

@dataclass
class MountedImage:
    set_name: str
    disk_filename: str
    backing_path: Path                 # path real del .img (puede ser /tmp/... si session)
    read_only: bool
    is_session: bool

@dataclass
class HostActivity:
    last_read_ts: float | None = None
    last_write_ts: float | None = None

@dataclass
class AppState:
    sets: list[FloppySet]              # escaneado del FS, refrescado por inotify
    cursor_screen: ScreenKind          # SPLASH, ROOT, DISKS, MOUNTED, ACTIVITY, CONFIRM_EJECT
    cursor_set_index: int = 0
    cursor_disk_index: int = 0
    mounted: MountedImage | None = None
    activity: HostActivity | None = None
    mute: bool = False
```

### 3.5 Estructura de directorios del proyecto

```
usb-floppy-pi/
├── src/usb_floppy_pi/
│   ├── __main__.py              # entry point, arranca asyncio
│   ├── core/
│   │   ├── state.py
│   │   ├── event_bus.py
│   │   └── config.py
│   ├── hardware/
│   │   ├── buttons.py
│   │   ├── lcd.py
│   │   └── audio.py
│   ├── gadget/
│   │   ├── controller.py        # configfs writer
│   │   └── activity.py
│   ├── storage/
│   │   └── library.py           # scanner + inotify watcher
│   ├── web/
│   │   ├── api.py               # FastAPI app
│   │   ├── routes.py
│   │   └── static/              # HTML/CSS/JS de la UI web
│   └── ui/
│       └── menus.py             # state machine del menú LCD
├── deploy/
│   ├── systemd/usb-floppy-pi.service
│   ├── samba/smb.conf.j2
│   ├── boot/config.txt.patch    # dwc2, libcomposite, i2c, hw PWM
│   └── install.sh               # script de instalación inicial
├── tests/
│   ├── unit/
│   └── integration/             # los que se puedan correr sin hardware
├── docs/
│   └── superpowers/specs/       # este documento
├── pyproject.toml
└── README.md
```

## 4. UI física

### 4.1 Esquema de botones (2 botones con tiempo de pulso)

Umbrales unificados (medidos al **release** del botón salvo "dual-held"):

| Gesto | Duración | Notas |
|-------|----------|-------|
| `short` | release <500ms | Acción primaria del botón |
| `long` | release ≥500ms y <3000ms | Acción secundaria del botón |
| `very_long` | release ≥3000ms | Solo tiene significado en EJECT |
| `double` | 2 `short` con <400ms entre release y siguiente press | Implica que `short` real fira con ~400ms de delay (esperamos por si llega el segundo) |
| `dual_held` | NAV y EJECT ambos pressed simultáneamente ≥2000ms | Fira *mientras se sostiene*, no en release. Bloquea siguientes eventos hasta release de ambos. |

Mapeo a acciones por botón:

| Botón | Gesto | Acción |
|-------|-------|--------|
| **NAV** | `short` | Siguiente entrada |
| NAV | `long` | Anterior entrada |
| NAV | `double` | Saltar 10 entradas |
| NAV | `very_long` | (sin asignar — equivale a `long`) |
| **EJECT** | `short` | Seleccionar / confirmar (depende del estado) |
| EJECT | `long` | Volver atrás / abrir confirmación de eject |
| EJECT | `very_long` | Forzar eject (sin pasar por confirmación) |
| EJECT | `double` | (sin asignar — equivale a `short`) |
| **NAV+EJECT** | `dual_held` | Toggle mute |

Decodificación: state machine por botón, polling cada 20ms.

**Trade-off del double-press:** detectar `double` requiere esperar 400ms tras el release de un `short` antes de despachar la acción "single short", por si llega un segundo press. Esto introduce ~400ms de latencia perceptible en navegación. Se considera aceptable para sets pequeños; si resulta molesto en uso real, se elimina `double` y se acelera `short`.

### 4.2 Máquina de estados del menú

```
SPLASH (boot+2s) → ROOT

ROOT (lista de sets):
  NAV short  → siguiente set
  NAV long   → anterior set
  NAV double → +10 (con wrap-around)
  EJECT short → si set tiene 1 disco: MOUNT → MOUNTED
              → si set tiene >1 disco: DISKS
  EJECT long / very_long → no-op + sonido `denied` (no hay nivel superior)

DISKS (sub-menú dentro de un set):
  NAV short / long / double → navegar (siguiente / anterior / +10)
  EJECT short → MOUNT → MOUNTED
  EJECT long / very_long → ROOT

MOUNTED (imagen activa):
  NAV short → siguiente disco del mismo set (remount, ~200ms)
  NAV long  → disco anterior del mismo set
  NAV double → siguiente disco saltando 10 (irrelevante en sets típicos)
  EJECT short → no-op (beep "denegado" suave: 100ms 300Hz)
  EJECT long → CONFIRM_EJECT
  EJECT very_long → eject + ROOT (sin confirmación)
  [host I/O] → ACTIVITY

ACTIVITY:
  [idle 500ms] → MOUNTED

CONFIRM_EJECT:
  EJECT short → confirmar → ROOT
  NAV * → cancelar → MOUNTED

[cualquier estado]:
  NAV+EJECT 2s → toggle mute
```

**Punto clave:** `NAV short` en estado MOUNTED cambia al siguiente disco del mismo set sin pasar por menús. Esto permite responder a "Insert disk 2" durante un instalador multi-disco con un solo click.

### 4.3 Pantallas LCD (16x2)

```
SPLASH:                     ROOT (lista de sets):
┌────────────────┐          ┌────────────────┐
│ USB Floppy Pi  │          │ DOS 6.22    RO │
│ booting....    │          │ ▶ 03/12     ⏏ │
└────────────────┘          └────────────────┘

DISKS (multi-disk set):     MOUNTED (single-disk):
┌────────────────┐          ┌────────────────┐
│ DISK002.img    │          │ Win98 Boot     │
│ 02/03    [RO] ⏏│          │ [RW] mounted ● │
└────────────────┘          └────────────────┘

MOUNTED (multi-disk):       ACTIVITY (durante I/O):
┌────────────────┐          ┌────────────────┐
│ DOS 6.22  2/3  │          │ DOS 6.22  2/3  │
│ DISK002 [RO] ●│          │ DISK002 R/W ●●│
└────────────────┘          └────────────────┘

CONFIRM_EJECT:
┌────────────────┐
│ Eject? ⏏ = OK │
│ ◀▶ = cancel    │
└────────────────┘
```

Strings >16 chars se hacen scroll horizontal cada 800ms.

### 4.4 Mapa de sonidos del buzzer

**Modelo mental:** un floppy 3.5" real solo hace ruido **durante el acceso**. En reposo (motor parado) es silencioso. El sonido reconocible es:

1. **Spin-up del motor** al iniciar acceso (chirp ascendente)
2. **Click del head load** cuando los cabezales bajan al disco
3. **Clack del stepper** cada vez que el cabezal cambia de track (no por byte ni por sector — solo por track)
4. **Whirr suave del motor** durante el acceso sostenido (casi inaudible, opcional)
5. **Spin-down** cuando para el motor tras un periodo de idle

#### Sonidos de emulación de floppy (fidelidad heurística — Fase 3)

Como `g_mass_storage` no expone el LBA de cada I/O, inferimos los "seeks" por el patrón temporal de actividad:

| Evento | Detector | Sonido |
|--------|----------|--------|
| `motor_spin_up` | primer I/O tras ≥3s de idle (o tras `mount`) | chirp 200→500Hz en 600ms |
| `head_load` | inmediatamente tras `motor_spin_up` | clack 150Hz 60ms |
| `track_seek` (heurística) | I/O reanudado tras pausa de ≥80ms (y motor ya gira) | clack 150Hz 60ms |
| `multi_track_seek` (heurística) | ráfaga sostenida >200KB/s detectada | 2-3 clacks distribuidos a 80ms intervalo |
| `motor_running` (opcional, low-vol) | mientras hay I/O activo | whirr 400Hz modulado ±5% a volumen muy bajo |
| `motor_spin_down` | sin I/O durante ≥3s | chirp 400→100Hz en 800ms |
| `image_mounted` | montaje, sin I/O todavía | (silencio — el floppy real al insertarse tampoco suena hasta el primer access) |
| `image_eject` | eject manual | spin-down explícito + "tick" mecánico final |

#### Sonidos de UI (no emulación; siempre presentes salvo mute)

| Evento | Sonido |
|--------|--------|
| `boot_ready` | melodía POST-OK (3 notas ascendentes), bloqueante |
| `nav_move` | click 50ms 2kHz |
| `image_select` | "tick" mecánico (50ms 800Hz + 30ms 400Hz) |
| `error` | dos tonos 200Hz (200ms ON, 100ms OFF, 200ms ON), bloqueante |
| `denied` | beep grave 100ms 300Hz |
| `mute_toggle_on` | "pop" descendente (200ms) — último sonido antes del silencio |
| `mute_toggle_off` | "pop" ascendente (200ms) — primer sonido al desmutearse |

#### Limitación conocida de la heurística

Sin información de LBA real, dos casos no se modelan correctamente:

- Una lectura grande **del mismo track** suena con clacks falsos si tiene pausas naturales del host
- Un seek **dentro del mismo track** no genera clack (no debería, así que esto es accidentalmente correcto)
- Un seek genuino sin pausa perceptible (lectura back-to-back en otro track) **no se detecta** y se omite el clack

En la práctica, los patrones I/O típicos de DOS/Win98 (`dir A:`, copiar un fichero, instalador navegando entre directorios) producen muchas pausas naturales y la heurística suena convincente. Si la fidelidad no satisface, ver Fase 4 (LBA real vía `blktrace` o eBPF tracepoint sobre el loop device).

### 4.5 Detección de actividad del host

`gadget.activity` polea cada **20ms** los contadores de bloques de la backing block device del LUN (vía `/sys/class/block/loopX/stat`). Mantiene un pequeño state machine para alimentar tanto al renderer LCD como al motor de sonido:

```
estado IDLE (motor parado, silencio)
  └─ Δ read|write > 0 → ACTIVE + emit motor_spin_up + head_load
estado ACTIVE
  ├─ pausa entre Δ ≥ 80ms y luego Δ > 0 → emit track_seek
  ├─ tasa de bytes > 200 KB/s sostenida 100ms → emit multi_track_seek
  ├─ pausa Δ = 0 durante 500ms → ACTIVITY indicator off en LCD (pero motor sigue)
  └─ pausa Δ = 0 durante 3000ms → IDLE + emit motor_spin_down
```

Cambios de contador rápidos (Δ > 0 cada poll) emiten `host_io` al bus, que el renderer LCD usa para parpadear el indicador `●●` en estado MOUNTED/ACTIVITY. El motor de sonido se suscribe al mismo bus para los eventos heurísticos descritos en 4.4.

## 5. Hardware

### 5.1 BOM

| Componente | Modelo/Especificación | Cantidad |
|-----------|----------------------|---------|
| SBC | Raspberry Pi Zero 2W | 1 |
| Display | LCD 1602 con backpack I2C (PCF8574) | 1 |
| Botones | Momentary push-button 12mm | 2 |
| Buzzer | Piezo **pasivo** (no activo) | 1 |
| microSD | ≥ 8 GB clase 10 | 1 |
| Cable | USB-A ↔ micro-USB de **datos** (no solo carga) | 1 |

### 5.2 Pinout (header 40 pines)

| Función | GPIO (BCM) | Pin físico | Notas |
|---------|-----------|-----------|-------|
| LCD VCC | — | 4 (5V) | LCD necesita 5V para backlight/contraste |
| LCD GND | — | 6 (GND) | |
| LCD SDA | GPIO 2 | 3 | I2C bus 1 — ver nota de niveles abajo |
| LCD SCL | GPIO 3 | 5 | |

**⚠️ Nota sobre niveles I2C (LCD a 5V con Pi a 3V3):**

El backpack PCF8574 alimenta sus pull-ups de I2C a la misma VCC que el LCD (5V). El GPIO del Pi Zero 2W es 3V3 y **no es 5V-tolerante**. Aunque mucha gente conecta directo y "funciona", está fuera de spec y puede dañar el Pi a largo plazo.

Opciones de mitigación, en orden de robustez:

1. **(Recomendado)** Insertar un level-shifter I2C bidireccional (e.g. PCA9306, TXS0108E, o módulo "I2C logic level converter" de 4 canales) entre las líneas SDA/SCL del Pi y las del backpack.
2. Desoldar los pull-ups del backpack y añadir pull-ups externos de 4.7kΩ a 3V3 del Pi (modificación del módulo).
3. Verificar con multímetro la tensión real del bus en reposo: si el backpack tiene pull-ups internos débiles y el Pi también activa sus pull-ups internos a 3V3, la línea puede quedarse en torno a 3.3V (esto pasa con algunos clones). Si es el caso, conectar directo es relativamente seguro.

El install script debe imprimir esta advertencia y pedir confirmación explícita antes de continuar.
| BTN_NAV | GPIO 17 | 11 | otra patilla a GND, pull-up interno |
| BTN_EJECT | GPIO 27 | 13 | otra patilla a GND, pull-up interno |
| Buzzer (+) | GPIO 18 | 12 | PWM0 hardware |
| Buzzer (−) | — | 14 (GND) | piezo directo, sin transistor para ≤25mA |

### 5.3 Configuración de boot

`/boot/firmware/config.txt` (añadir):
```
dtoverlay=dwc2
dtparam=i2c_arm=on
dtparam=audio=off
```

`/boot/firmware/cmdline.txt` (añadir al final de la línea):
```
modules-load=dwc2
```

### 5.4 Secuencia de boot

```
[systemd]
  ├─ pigpiod.service              ← daemon de hw PWM
  ├─ smbd.service                  ← Samba
  └─ usb-floppy-pi.service         ← After=pigpiod.service smbd.service
        │
        ├─ 1. Carga /etc/usb-floppy-pi/config.json
        ├─ 2. Escanea /home/pi/floppies/ → lista de FloppySets
        ├─ 3. Crea gadget USB en /sys/kernel/config/usb_gadget/floppy/
        │     (parámetros detallados en §5.5)
        ├─ 4. Activa gadget escribiendo UDC name a /sys/.../UDC
        ├─ 5. Inicializa LCD, pinta SPLASH
        ├─ 6. Inicializa GPIO botones, arranca poller
        ├─ 7. Inicializa pigpio, melodía POST
        ├─ 8. Arranca FastAPI en puerto 80 (con `setcap cap_net_bind_service`)
        └─ 9. Loop principal asyncio
```

### 5.5 Configuración del USB gadget (configfs)

Estructura completa que crea `gadget.controller` en `/sys/kernel/config/usb_gadget/floppy/`:

#### Descriptor del dispositivo

| Parámetro | Valor | Notas |
|-----------|-------|-------|
| `idVendor` | `0x0525` | Linux Foundation (g_mass_storage default) |
| `idProduct` | `0xA4A5` | Mass Storage Gadget |
| `bcdDevice` | `0x0001` | versión del firmware (cosmético) |
| `bcdUSB` | `0x0200` | USB 2.0 |
| `strings/0x409/manufacturer` | `"Linux Foundation"` | EN-US |
| `strings/0x409/product` | `"USB Floppy"` | |
| `strings/0x409/serialnumber` | derivado del MAC del Pi | único por unidad |

> **Nota sobre VID/PID — historia y trade-off:** Originalmente intentamos spoofear `0x0644:0x0000` (TEAC FD-05PUW) pensando que ayudaría a la BIOS retro a reconocernos como `A:` por legacy emulation. **No funcionó:** Windows moderno rechaza el PID `0x0000` (es técnicamente "reservado" en la spec USB) con Device Manager Code 10. Pasamos a los IDs estándar de Linux Foundation que es lo que usa el `g_mass_storage` legacy y proyectos similares como Pi-Floppy. El INQUIRY string SCSI (ver §LUN abajo) sigue diciendo `TEAC FD-05PUW` — eso es lo que la BIOS de la H55 mira para identificarnos como USB-FDD, no el USB descriptor.

#### Configuración 1 (única)

| Parámetro | Valor |
|-----------|-------|
| `bmAttributes` | `0xC0` (self-powered, no remote wakeup) |
| `MaxPower` | `2` (4mA — declaramos consumo mínimo porque la energía viene del PC) |
| `strings/0x409/configuration` | `"USB Floppy Config"` |

#### Función `mass_storage.usb0` (parámetros a nivel función)

| Parámetro | Valor | Notas |
|-----------|-------|-------|
| `stall` | `1` | comportamiento estándar de error |
| `num_buffers` | `2` | suficiente para 1.44MB; reduce uso de RAM |

#### LUN 0 (el único — un solo "floppy" expuesto)

Path: `mass_storage.usb0/lun.0/`

| Parámetro | Valor | Razón |
|-----------|-------|-------|
| `file` | path al `.img` montado (o vacío al boot si no hay) | backing del LUN |
| `ro` | `0` o `1` según marker `ro` del set + flag de session | controla read-only desde el host |
| **`removable`** | **`1`** | **crítico**: sin esto Windows lo ve como disco fijo, no permite eject, BIOS no lo expone como `A:` |
| **`cdrom`** | **`0`** | explícito (default ya es 0) — somos floppy, no CD |
| **`nofua`** | **`0`** | honrar el flag Force Unit Access del host: cuando Win98/DOS pide flush, escribe a disco de verdad. Crítico para mitigar corrupción por apagón. |
| **`inquiry_string`** | **`"TEAC    FD-05PUW         3000"`** | 28 chars (8 vendor + 16 product + 4 rev). Esto es lo que hace que el BIOS de la H55 nos reconozca como USB-FDD y nos exponga como `A:` en legacy emulation. |

#### Limitación: subclass/protocol no son UFI

El gadget `mass_storage` del kernel Linux solo soporta:
- `bInterfaceSubClass = 0x06` (SCSI transparent)
- `bInterfaceProtocol = 0x50` (Bulk-Only Transport / BBB)

Un USB-FDD "puro" como el TEAC FD-05PUW usa subclass `0x04` (UFI) y protocol `0x00` (CBI). El kernel Linux **hardcodea** SubClass=06; no hay forma de cambiarlo desde configfs. Implementarlo requeriría un gadget driver UFI custom en C (Phase 4).

**Comportamiento práctico observado:**
- **Con disco montado**: el host hace `READ_CAPACITY`, ve 1.44 MB, lo identifica como floppy ✓
- **Sin disco** (`lun.0/file` vacío): el host no puede leer capacidad, **Windows moderno cae a "USB removable drive genérico"** y pierde el tipo "Floppy".
- **DOS / Win98 vía BIOS legacy emulation**: la BIOS opera a nivel INT 13h y enumera el dispositivo una sola vez al boot. Si al momento del boot hay un disco montado (que es lo que garantizamos con el pre-attach + `last_mounted` restore en §5.4), la BIOS asigna `A:` USB-FDD y mantiene esa identidad durante toda la sesión, independiente de mount/eject posteriores.

**Mitigación documentada para Windows moderno:** mantener siempre un disco montado (mental model "Gotek con disquetes virtuales", no "drive vacío con cable USB"). El Phase 4 puede agregar opcionalmente un `blank.img` mounteable o un gadget UFI custom para resolver este caso permanentemente.

#### Cambio de imagen sin re-enumerar

Como todas las imágenes son 1.44MB y el LUN ya está marcado `removable=1`, **el cambio de imagen NO requiere destruir/recrear el gadget**. La secuencia es:

1. Escribir cadena vacía a `lun.0/file` → el host ve un "disquete eyectado"
2. (Opcional, ~150ms de espera para que el host procese el eject)
3. Escribir el nuevo path a `lun.0/file` → el host ve un nuevo disquete insertado

El host (Win98/DOS) recibe los SCSI sense codes apropiados (`UNIT_ATTENTION`, `MEDIUM_NOT_PRESENT`, etc.) y refresca su vista del medio sin re-enumerar el bus USB. Esto es exactamente lo que pasa cuando cambias un floppy físico real.

### 5.6 Robustez ante apagón abrupto

Sin UPS, mitigamos con software:

| Riesgo | Mitigación |
|--------|-----------|
| Corrupción de la SD del Pi | ext4 con `commit=1,barrier=1`. La SD apenas se escribe en runtime (logs van a journald in-memory). |
| Corrupción de un `.img` writable | LUN configurado con `nofua=0` (ver §5.5): el kernel hace flush real cuando el host pide FUA. Adicionalmente, escrituras a `config.json` y otros metadatos usan `fsync` explícito. |
| Pérdida de `config.json` | Escritura atómica: `tmp + fsync + rename`. |
| Sets corruptos por escritura interrumpida | Validamos el `.img` al arrancar: si el tamaño cambió o falla un sanity-check, marcamos el set como "needs-attention" en LCD/web pero no lo bloqueamos. |

**Aceptado explícitamente:** un fichero `.img` aún puede corromperse si el PC corta corriente exactamente durante una escritura del filesystem FAT12 del propio disquete. Eso es exactamente como un floppy real — coincide con el modelo mental del usuario.

### 5.7 WiFi

Asumimos configurado al flashear con `rpi-imager` (incluye GUI para credenciales WiFi). El proyecto **no** implementa AP-mode de fallback en MVP. Si más adelante se necesita, se añade en fase 4.

## 6. Web UI / API

### 6.1 Tecnología

- FastAPI + uvicorn dentro del mismo event loop asyncio
- HTML estático + vanilla JavaScript (sin framework JS — el proyecto no lo justifica)
- Servido en puerto 80 con `setcap cap_net_bind_service=+ep` sobre el binario de Python (no corremos como root)
- mDNS via avahi (servicio del sistema): `floppy.local` o `usb-floppy-pi.local`

### 6.2 Endpoints

| Método | Ruta | Función |
|--------|------|---------|
| GET | `/` | Página principal (HTML) |
| GET | `/api/sets` | Lista de FloppySets con sus disks |
| GET | `/api/state` | Estado actual (imagen montada, mute, etc.) |
| POST | `/api/mount` | `{set, disk, session?}` → monta |
| POST | `/api/eject` | Desmonta |
| POST | `/api/upload` | Sube un `.img`/`.ima`/`.imz` a una carpeta (multipart). `.ima` se renombra y `.imz` se extrae antes de guardar (ver 2.2). |
| POST | `/api/sets/{name}/readonly` | `{ro: bool}` → crea/borra el marker |
| POST | `/api/mute` | Toggle mute |
| GET | `/api/log` | Últimas N entradas de eventos (desde journald) |

### 6.3 UI

- Lista de sets agrupados por carpeta
- Indicador del disco montado (highlight)
- Botón "Mount" / "Eject" / "Mount as session"
- Drag & drop para subir un `.img` a una carpeta existente o crear una nueva
- Toggle mute global
- Log del día (últimos eventos)

## 7. Samba

`/etc/samba/smb.conf` (renderizado desde plantilla en `deploy/samba/smb.conf.j2`):

```
[global]
   workgroup = WORKGROUP
   server string = USB Floppy Pi
   netbios name = FLOPPY
   security = user
   map to guest = bad user

[floppies]
   path = /home/pi/floppies
   browseable = yes
   read only = no
   writable = yes
   guest ok = no
   valid users = floppy
   create mask = 0664
   directory mask = 0775
```

Usuario Samba `floppy` creado por el script de instalación con password configurado por el usuario.

## 8. Plan de fases

### Fase 1 — MVP "headless" (sin botones, sin audio, sin LCD)
**Objetivo:** que el PC retro vea el floppy emulado y se pueda cambiar de imagen desde la web.

- Setup `dwc2` + configfs en boot
- `gadget.controller`: crear/montar/cambiar/eject vía API interna
- `storage.library`: scanner de `/home/pi/floppies/` + watcher inotify + normalización de formatos (`.ima`/`.imz` → `.img`)
- `web_api`: REST + UI mínima (HTML+vanilla JS) — listar sets, mount, eject, upload (acepta `.img`/`.ima`/`.imz`)
- Samba share configurado
- `core.config` persistencia, last_mounted al boot
- systemd unit
- Script `install.sh`

**Entregable:** booteás el Pi, lo enchufás al PC retro, ves el floppy en DOS/Win98. Cambiás imagen desde el móvil.

### Fase 2 — UI física (LCD + botones)
**Objetivo:** cambiar imagen sin web; frente del floppy funcional.

- `hardware.lcd` con backend I2C
- `hardware.buttons` con decodificador de pulsos (corto/largo/doble/dual-press)
- `ui.menus` máquina de estados completa
- Renderizado de las 6 pantallas

**Entregable:** el dispositivo es autónomo; el móvil ya no es necesario para uso normal.

### Fase 3 — Audio
**Objetivo:** inmersión retro completa.

- `hardware.audio` con backend pigpio (PWM hardware)
- `gadget.activity` polling de sysfs
- Sound engine con loops + eventos puntuales
- Mute toggle (NAV+EJECT 2s)

**Entregable:** suena como un floppy de verdad cuando el PC lee/escribe.

### Fase 4 — Pulido (post-MVP, opcional)
- **Sonido de seek con fidelidad LBA real** — reemplazar la heurística de 4.4 por captura de LBA por request (vía `blktrace` parser o eBPF tracepoint sobre el loop device) y conversión LBA→track CHS (`track = LBA / 36` para 1.44MB). Un clack por cambio de track real.
- AP-mode fallback de WiFi
- Modo session-mount expuesto en web
- Overlayfs en `/` (hardening SD)
- Web UI mejorada (drag&drop múltiple, papelera, batch ops)
- Métricas / dashboard pequeño

## 9. Testabilidad

Cada módulo de hardware tiene una interfaz abstracta + dos backends:

```python
class LcdBackend(Protocol):
    def write(self, line: int, text: str) -> None: ...

class I2cLcdBackend:           # real: usa RPLCD
    ...

class TerminalLcdBackend:      # mock: imprime en stdout
    ...
```

Idem para `ButtonsBackend` (real GPIO vs. teclas del terminal), `AudioBackend` (pigpio vs. archivo `.wav` generado), `GadgetBackend` (configfs vs. fichero loopback). Esto permite ejecutar el ~90% del código en un PC para iterar rápido sin desplegar al Pi.

Tests unitarios en `tests/unit/` (puros, sin filesystem ni red), tests de integración en `tests/integration/` (con tmpfs para `/home/pi/floppies/`, mocks de hardware).

## 10. Fuera de alcance

- Emulación de floppy físico vía conector Shugart 34-pin
- Soporte de formatos exóticos (`.adf`, `.hfe`, `.imd`, `.dsk`)
- Capacidades distintas a 1.44 MB (no 720KB, no 2.88MB, no superfloppy)
- Multi-LUN (presentar múltiples floppies simultáneos al host)
- Auto-troceado de imágenes >1.44MB en multi-disk virtual
- Edición de contenido de las imágenes desde la web
- Encripción de las imágenes
- Acceso remoto al dispositivo desde fuera de la LAN
