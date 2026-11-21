"""Generic Berkeley SPICE3 / ngspice reader (shared base, no vendor quirks)."""

from __future__ import annotations

from .base import BaseReader


class Spice3Reader(BaseReader):
    dialect = "spice3"
