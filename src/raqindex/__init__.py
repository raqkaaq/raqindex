import logging 

def __getattr__(name):
    if name == "OpenAlexClient":
        from .client import OpenAlexClient
        return OpenAlexClient
    raise AttributeError(f"module {__name__} has no attribute {name}")

__all__ = ["OpenAlexClient"]

import logging.config

LOGGING_CONFIG = {
    "version": 1,
    "handlers": {
        "default": {
            "class": "logging.StreamHandler",
            "formatter": "http",
            "stream": "ext://sys.stderr"
        }
    },
    "formatters": {
        "http": {
            "format": "%(levelname)s [%(asctime)s] %(name)s - %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        }
    },
    'loggers': {
        'httpx2': {
            'handlers': ['default'],
            'level': 'WARNING',
        },
        'httpcore2': {
            'handlers': ['default'],
            'level': 'WARNING',
        },
        'hishel': {
            'handlers': ['default'],
            'level': 'WARNING',
        },
        'raqindex': {
            'handlers': ['default'],
            'level': 'DEBUG',   
        },
    }
}

logging.config.dictConfig(LOGGING_CONFIG)
