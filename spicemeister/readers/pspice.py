# Copyright 2026 Nick L
# SPDX-License-Identifier: Apache-2.0
"""PSpice / OrCAD reader.

PSpice shares the base grammar (brace-delimited expressions, ``.func``,
``VALUE=``/``TABLE`` behavioural sources, ``.model`` with parenthesised
parameters). ``.PROTECT``/``.UNPROTECT`` spans are isolated by the lexer and
surface as :class:`EncryptedCard`.
"""

from __future__ import annotations

from .base import BaseReader


class PSpiceReader(BaseReader):
    dialect = "pspice"
    dollar_comment = False      # PSpice/OrCAD: '$' begins a node name ($N_0001), not a comment
