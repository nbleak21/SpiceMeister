"""ngspice-native output profile: uppercase directives + ``PARAMS:``.

Matches the pcb-stack stdlib house style (``.SUBCKT``/``.MODEL``/``.ENDS`` with
``PARAMS:`` and ``{param}`` substitution), the friction-free end state that
loads without a ``.spiceinit``/``ngbehavior`` dialect setting.
"""

from __future__ import annotations

from ..core import ir
from .base import Profile, Writer

PROFILE = Profile(name="ngspice", kw_upper=True, banner_label="ngspice-native generic SPICE")


def render(netlist: ir.Netlist, banner: bool = True) -> str:
    return Writer(PROFILE).render(netlist, banner=banner)
