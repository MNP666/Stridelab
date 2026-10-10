"""Stridelab: decode .fit activity files with our own pure-Python reader."""

from .reader import FitParseError, Message, iter_messages

__all__ = ["FitParseError", "Message", "iter_messages"]
