"""Compatibility names for the shared endpoint metadata models."""

from raq_helpers.models import (
    Endpoint,
    EndpointOperation as Method,
    ModelReference as ReqRes,
    ResponseCollection,
)

__all__ = ["Endpoint", "Method", "ReqRes", "ResponseCollection"]
