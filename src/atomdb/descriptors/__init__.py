"""Pluggable, *exploratory* structure descriptors (not a permanent descriptor database)."""

from .base import Descriptor, get_descriptor, list_descriptors

__all__ = ["Descriptor", "get_descriptor", "list_descriptors"]
