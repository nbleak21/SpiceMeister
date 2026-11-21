"""High-level translate pipeline: text -> IR -> transforms -> output text."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from . import readers, transforms
from .core import ir
from .core.dialect import Detection, detect
from .writers import ltspice as _lt
from .writers import ngspice as _ng
from .writers import zener_snippet

_WRITERS = {"ltspice": _lt.render, "ngspice": _ng.render}


@dataclass
class Result:
    output: str
    netlist: ir.Netlist
    detection: Detection
    profile: str
    zener: Optional[str] = None
    warnings: List[str] = field(default_factory=list)


def get_writer(profile: str):
    try:
        return _WRITERS[profile]
    except KeyError:
        raise ValueError(f"unknown profile {profile!r}; choose from {sorted(_WRITERS)}")


def translate(text: str, from_dialect: str = "auto", profile: str = "ltspice",
              source_name: str = "", banner: bool = True,
              emit_zener: bool = False, lib_relpath: str = "") -> Result:
    det = detect(text)
    dialect = det.dialect if from_dialect == "auto" else from_dialect
    netlist = readers.read(text, dialect=dialect, source_name=source_name)
    transforms.run_all(netlist, target=profile)
    render = get_writer(profile)
    output = render(netlist, banner=banner)

    zener = None
    if emit_zener:
        subs = netlist.subckts()
        if subs:
            rel = lib_relpath or (source_name or "model") + ".lib"
            zener = zener_snippet.render_snippet(subs[0], rel)

    return Result(output=output, netlist=netlist, detection=det,
                  profile=profile, zener=zener, warnings=list(netlist.warnings))
