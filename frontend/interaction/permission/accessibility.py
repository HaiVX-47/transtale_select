from __future__ import annotations

from dataclasses import dataclass

import gi

gi.require_version("Atspi", "2.0")

from gi.repository import Atspi


@dataclass(frozen=True)
class AccessibilityCapability:
    module_available: bool
    bus_reachable: bool
    error_message: str | None = None


class AccessibilityPermission:
    """
    Checks whether the AT-SPI stack is available through
    GObject Introspection and whether the accessibility bus
    can be reached.
    """

    @staticmethod
    def check_capability() -> AccessibilityCapability:

        # --------------------------------------------------------
        # GI / Atspi typelib
        # --------------------------------------------------------

        try:
            desktop_count = Atspi.get_desktop_count()

        except Exception as exc:
            return AccessibilityCapability(
                module_available=True,
                bus_reachable=False,
                error_message=(
                    "AT-SPI bus is unreachable: "
                    f"{exc}"
                ),
            )

        # --------------------------------------------------------
        # Basic desktop sanity check
        # --------------------------------------------------------

        if desktop_count <= 0:
            return AccessibilityCapability(
                module_available=True,
                bus_reachable=True,
                error_message=(
                    "AT-SPI is available, but no desktop "
                    "instance was reported."
                ),
            )

        return AccessibilityCapability(
            module_available=True,
            bus_reachable=True,
            error_message=None,
        )