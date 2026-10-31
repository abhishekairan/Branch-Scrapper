"""Browser layer. Import the interface from here; the Zendriver backend is loaded lazily."""
from .backend import (BlockedError, BrowserPool, BrowserSlot, Cancelled, FrameCallback, PoolFactory,
                      SlotCrashed)

__all__ = ["BlockedError", "BrowserPool", "BrowserSlot", "Cancelled", "FrameCallback", "PoolFactory",
           "SlotCrashed", "default_pool_factory"]


def default_pool_factory(cfg, log, stop, workers):
    """Build the real (Zendriver) pool. Imported lazily so offline tests never load zendriver."""
    from .zendriver_backend import ZendriverPool
    return ZendriverPool(cfg, log, stop, workers)
