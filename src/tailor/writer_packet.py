"""Backward-compatibility shim.

Implementation moved to tailor.core_generation.writer_packet.
All imports from this path continue to work unchanged.
"""

from tailor.core_generation.writer_packet import build_writer_packet

__all__ = ["build_writer_packet"]
