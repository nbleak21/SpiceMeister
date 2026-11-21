"""LTspice output profile (default): lowercase directives, portable syntax."""

from __future__ import annotations

from ..core import ir
from .base import Profile, Writer

PROFILE = Profile(name="ltspice", kw_upper=False, banner_label="LTspice-compatible")


def render(netlist: ir.Netlist, banner: bool = True) -> str:
    return Writer(PROFILE).render(netlist, banner=banner)
