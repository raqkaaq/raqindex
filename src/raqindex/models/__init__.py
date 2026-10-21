__all__ = ["OQO", "XQuery", "Endpoint"]

def __getattr__(name):
    if name == "OQO":
        from .oqo import Model
        return Model

    if name == "XQuery":
        from .query import XQuery
        return XQuery
    
    if name == "Endpoint":
        from .endpoints import Endpoint
        return Endpoint

    raise AttributeError(name)


