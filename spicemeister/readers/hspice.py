"""HSPICE / Synopsys reader.

Key HSPICE traits, all handled by the base pipeline:
  * single-quoted expressions ``'...'`` (stripped to brace-free Expr),
  * ``$`` inline comments (handled by the lexer),
  * ``.option``/``.options``, ``.global``, ``.temp``,
  * ``.lib 'file' section`` references,
  * behavioural ``VOL=``/``CUR=`` keywords on E/G sources.

The expression-delimiter transform later normalises any residual quotes, and
the ``functions`` transform maps HSPICE-only functions (e.g. ``pow``).
"""

from __future__ import annotations

from .base import BaseReader


class HSpiceReader(BaseReader):
    dialect = "hspice"
