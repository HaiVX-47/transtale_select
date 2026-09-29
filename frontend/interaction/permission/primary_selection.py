import subprocess
import shutil
from dataclasses import dataclass
from .environment import EnvironmentDetector

@dataclass(frozen=True)
class PrimarySelectionCapability:
    is_available: bool
    backend_tool: str | None = None
    error_message: str | None = None

class PrimarySelectionPermission:
    """
    Checks if primary selection is readable (xclip/xsel for X11, wl-paste for Wayland).
    """
    
    @staticmethod
    def check_capability() -> PrimarySelectionCapability:
        if EnvironmentDetector.is_wayland():
            if shutil.which("wl-paste"):
                return PrimarySelectionCapability(is_available=True, backend_tool="wl-paste")
            return PrimarySelectionCapability(
                is_available=False,
                error_message="wl-paste not found. Please install wl-clipboard."
            )
        else:
            if shutil.which("xclip"):
                return PrimarySelectionCapability(is_available=True, backend_tool="xclip")
            elif shutil.which("xsel"):
                return PrimarySelectionCapability(is_available=True, backend_tool="xsel")
            return PrimarySelectionCapability(
                is_available=False,
                error_message="Neither xclip nor xsel found."
            )
