import os

class EnvironmentDetector:
    """
    Detects the current desktop environment and display server.
    """
    
    @staticmethod
    def is_wayland() -> bool:
        return os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"

    @staticmethod
    def is_x11() -> bool:
        session_type = os.environ.get("XDG_SESSION_TYPE", "").lower()
        if session_type == "x11":
            return True
        # Fallback if XDG_SESSION_TYPE is missing: check DISPLAY but only if not Wayland
        if session_type == "wayland":
            return False
        return "DISPLAY" in os.environ
        
    @staticmethod
    def get_desktop_session() -> str:
        return os.environ.get("XDG_CURRENT_DESKTOP", "unknown").lower()
