from .environment import Environment

__all__ = ["Environment", "OpenAlexClient"]

def __getattr__(name):
    if name == "OpenAlexClient":
        from .client import OpenAlexClient
        return OpenAlexClient
    raise AttributeError(name)