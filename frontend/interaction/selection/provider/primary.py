import subprocess
import logging
from .base import BaseSelectionProvider
from ..result import SelectionResult, SelectionLine
from ...permission.primary_selection import PrimarySelectionPermission

logger = logging.getLogger(__name__)
CLIPBOARD_TIMEOUT_SECONDS = 0.5

class PrimarySelectionProvider(BaseSelectionProvider):
    """
    Extracts text using the primary selection clipboard (middle click clipboard).
    
    Dependencies: 
        - wl-paste (Wayland)
        - xclip or xsel (X11)
    
    Inputs: None
    Outputs: SelectionResult
    
    Coordinate System: 
        - geometry is always None (unavailable via standard clipboard API)
    
    Failure Behavior:
        - Returns None if wl-paste/xclip/xsel is not installed.
        - Returns None if the subprocess fails or returns empty text.
    """
    
    def __init__(self):
        self.capability = PrimarySelectionPermission.check_capability()

    def is_available(self) -> bool:
        return self.capability.is_available

    def extract(self) -> SelectionResult | None:
        if not self.is_available():
            return None
            
        try:
            if self.capability.backend_tool == "wl-paste":
                result = subprocess.run(
                    ["wl-paste", "--primary"], 
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=CLIPBOARD_TIMEOUT_SECONDS,
                )
            elif self.capability.backend_tool == "xclip":
                result = subprocess.run(
                    ["xclip", "-o", "-selection", "primary"], 
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=CLIPBOARD_TIMEOUT_SECONDS,
                )
            elif self.capability.backend_tool == "xsel":
                result = subprocess.run(
                    ["xsel", "-o", "--primary"], 
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=CLIPBOARD_TIMEOUT_SECONDS,
                )
            else:
                return None
                
            raw_text = result.stdout
            if not raw_text:
                return None
                
            return self._normalize_text(raw_text)
            
        except subprocess.TimeoutExpired:
            logger.debug(
                "Primary selection command timed out after %.1f seconds.",
                CLIPBOARD_TIMEOUT_SECONDS,
            )
            return None
        except (subprocess.CalledProcessError, OSError):
            return None

    def _normalize_text(self, text: str) -> SelectionResult:
        """
        Preserves newlines and calculates offsets accurately.
        """
        raw_lines = text.split('\n')
        
        selection_lines = []
        current_offset = 0
        
        for i, line_text in enumerate(raw_lines):
            start = current_offset
            end = current_offset + len(line_text)
            
            selection_lines.append(SelectionLine(
                index=i,
                text=line_text,
                start_offset=start,
                end_offset=end,
                rect=None,
                words=(),
                fragments=()
            ))
            
            current_offset = end + 1 # +1 for newline character
            
        return SelectionResult(
            text=text,
            lines=tuple(selection_lines),
            bounds=None,
            source="primary_selection"
        )
