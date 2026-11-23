# Copyright 2026 Nick L
# SPDX-License-Identifier: Apache-2.0
"""Dialect reader registry."""

from __future__ import annotations

from ..core import ir
from ..core.dialect import detect
from .base import BaseReader
from .hspice import HSpiceReader
from .pspice import PSpiceReader
from .spectre import SpectreReader
from .spice3 import Spice3Reader

_READERS = {
    "spice3": Spice3Reader,
    "ngspice": Spice3Reader,
    "ltspice": Spice3Reader,
    "pspice": PSpiceReader,
    "hspice": HSpiceReader,
    "spectre": SpectreReader,
}


def get_reader(dialect: str) -> BaseReader:
    cls = _READERS.get(dialect.lower())
    if cls is None:
        raise ValueError(f"unknown dialect {dialect!r}; known: {sorted(_READERS)}")
    return cls()


def read(text: str, dialect: str = "auto", source_name: str = "") -> ir.Netlist:
    """Parse ``text`` into a Netlist, auto-detecting the dialect if asked."""
    if dialect == "auto":
        dialect = detect(text).dialect
    return get_reader(dialect).read(text, source_name=source_name)
