"""SpiceMeister — translate vendor SPICE models to LTspice-compatible models.

A parse -> IR -> transform -> emit -> verify pipeline for converting PSpice,
HSPICE, Spectre and generic SPICE3 device models and subcircuits into clean
LTspice / ngspice-loadable form, with optional round-trip validation through
ngspice and a bridge to the REA toolkit for opaque binary model containers.
"""

__version__ = "0.1.0"

DIALECTS = ("pspice", "hspice", "spectre", "spice3", "ltspice")

__all__ = ["__version__", "DIALECTS"]
