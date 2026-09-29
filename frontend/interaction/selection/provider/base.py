import abc
from ..result import SelectionResult

class BaseSelectionProvider(abc.ABC):
    """
    Abstract base class for all selection extractors.
    
    Dependencies: None
    Inputs: None
    Outputs: SelectionResult | None
    Coordinate System: Implementations must convert their native coordinates 
                       to global screen coordinates for SelectionResult.
    Failure Behavior: Return None if unavailable or if extraction fails.
    """
    
    @abc.abstractmethod
    def extract(self) -> SelectionResult | None:
        """
        Attempts to extract the current selection.
        Must return a valid SelectionResult or None if it fails.
        """
        pass

    @abc.abstractmethod
    def is_available(self) -> bool:
        """
        Returns True if this provider has the required capabilities and is usable.
        """
        pass
