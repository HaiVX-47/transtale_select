import logging
from .result import SelectionResult
from .provider import AtspiProvider, PrimarySelectionProvider

logger = logging.getLogger(__name__)

class SelectionResolver:
    """
    Orchestrates providers to extract selection.
    
    Dependencies: All Provider modules in `provider/`.
    Inputs: None
    Outputs: SelectionResult | None
    Coordinate System: Inherited from the successful provider (global screen coordinates).
    Failure Behavior: Falls back through the provider chain based on clear policies.
    """
    
    def __init__(self):
        # Priority order is defined by the list order
        self.providers = [
            AtspiProvider(),
            PrimarySelectionProvider(),
        ]
        
    def resolve(self, *, include_geometry: bool = True) -> SelectionResult | None:
        """
        Attempts to extract selection using available providers in priority order.
        """
        for provider in self.providers:
            if not provider.is_available():
                continue
                
            # AT-SPI geometry is much more expensive than reading the selected
            # text. The monitor asks for text-only snapshots while it waits for
            # a stable selection, then performs one geometry pass at publish.
            try:
                if isinstance(provider, AtspiProvider):
                    result = provider.extract(include_geometry=include_geometry)
                else:
                    result = provider.extract()
            except Exception:
                logger.debug(
                    "Selection provider %s failed; trying the next provider: %s",
                    type(provider).__name__,
                    exc_info=True,
                )
                continue
            
            # Policy definition:
            # 1. A result is valid only when it contains non-whitespace text.
            # 2. Missing geometry does not invalidate a textual selection.
            # 3. Whitespace-only results are transient/empty and must not stop
            #    provider fallback.
            if result is not None and result.text.strip():
                return result
                
        return None
