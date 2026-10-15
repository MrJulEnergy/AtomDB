"""Pluggable, *exploratory* structure descriptors (not a permanent descriptor database)."""

from .base import AtomDescriptor, Descriptor, get_descriptor, list_descriptors

__all__ = ["AtomDescriptor", "Descriptor", "get_descriptor", "list_descriptors"]
