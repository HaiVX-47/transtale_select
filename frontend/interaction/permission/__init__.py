"""
Permission and capability subsystem.
"""
from .accessibility import AccessibilityPermission, AccessibilityCapability
from .primary_selection import PrimarySelectionPermission, PrimarySelectionCapability
from .manager import PermissionManager, Capabilities
from .environment import EnvironmentDetector

__all__ = [
    "AccessibilityPermission",
    "AccessibilityCapability",
    "PrimarySelectionPermission",
    "PrimarySelectionCapability",
    "PermissionManager",
    "Capabilities",
    "EnvironmentDetector",
]
