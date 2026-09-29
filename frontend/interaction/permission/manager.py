from dataclasses import dataclass
from .environment import EnvironmentDetector
from .accessibility import AccessibilityPermission, AccessibilityCapability
from .primary_selection import PrimarySelectionPermission, PrimarySelectionCapability

@dataclass(frozen=True)
class Capabilities:
    environment: str
    is_wayland: bool
    accessibility: AccessibilityCapability
    primary_selection: PrimarySelectionCapability

class PermissionManager:
    """
    Orchestrates all capability checks.
    """
    
    @staticmethod
    def evaluate_all() -> Capabilities:
        return Capabilities(
            environment=EnvironmentDetector.get_desktop_session(),
            is_wayland=EnvironmentDetector.is_wayland(),
            accessibility=AccessibilityPermission.check_capability(),
            primary_selection=PrimarySelectionPermission.check_capability()
        )
