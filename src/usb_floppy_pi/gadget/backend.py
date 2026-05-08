"""GadgetBackend protocol + MockBackend for testing."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class GadgetParams:
    id_vendor: int
    id_product: int
    bcd_device: int
    manufacturer: str
    product: str
    serial: str
    inquiry_string: str  # 28 chars: 8 vendor + 16 product + 4 revision


class GadgetBackend(Protocol):
    def create_gadget(self, params: GadgetParams) -> None: ...
    def destroy_gadget(self) -> None: ...
    def configure_lun(self, *, file: Path | None, ro: bool) -> None: ...
    def attach_to_udc(self) -> None: ...
    def detach_from_udc(self) -> None: ...

    # --- Phase 2 capabilities (optional; backends may return no-op) ---------

    def set_speed_preset(self, preset: str) -> None:
        """Set the floppy emulation speed preset (Phase 2.3+).
        Phase 1 backends return no-op."""
        return None

    def set_volume(self, volume: int) -> None:
        """Set buzzer volume 0..100 (Phase 2.4+)."""
        return None

    def set_mute(self, mute: bool) -> None:
        """Mute/unmute the buzzer (Phase 2.4+)."""
        return None

    def set_buzzer_enabled(self, enabled: bool) -> None:
        """Enable/disable buzzer master (Phase 2.4+)."""
        return None

    def get_metrics(self) -> dict:
        """Return a dict of runtime info: speed_preset, speed_read_kbps,
        volume, mute, buzzer, etc. Phase 1 backends return {}."""
        return {}


class MockBackend:
    """Records ops to memory for unit tests."""

    def __init__(self) -> None:
        self.created: bool = False
        self.attached: bool = False
        self.params: GadgetParams | None = None
        self.lun_file: Path | None = None
        self.lun_ro: bool = False
        self.ops_log: list[str] = []

    def create_gadget(self, params: GadgetParams) -> None:
        self.created = True
        self.params = params
        self.ops_log.append(f"create({params.product})")

    def destroy_gadget(self) -> None:
        self.created = False
        self.attached = False
        self.lun_file = None
        self.lun_ro = False
        self.ops_log.append("destroy")

    def configure_lun(self, *, file: Path | None, ro: bool) -> None:
        if not self.created:
            raise RuntimeError("configure_lun called before create_gadget")
        self.lun_file = file
        self.lun_ro = ro
        self.ops_log.append(f"configure_lun(file={file}, ro={ro})")

    def attach_to_udc(self) -> None:
        if not self.created:
            raise RuntimeError("attach_to_udc called before create_gadget")
        self.attached = True
        self.ops_log.append("attach")

    def detach_from_udc(self) -> None:
        self.attached = False
        self.ops_log.append("detach")
