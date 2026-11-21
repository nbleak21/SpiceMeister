#!/usr/bin/env bash
# SpiceMeister quick demo: detect -> translate -> validate.
set -euo pipefail
cd "$(dirname "$0")"
SM="../bin/smeister"

echo "== detect =="
"$SM" detect pspice_opamp.lib

echo; echo "== translate (LTspice profile) =="
"$SM" translate pspice_opamp.lib -o /tmp/pspice_opamp.ltspice.lib --report /tmp/pspice_opamp.report.md
sed -n '1,40p' /tmp/pspice_opamp.ltspice.lib

echo; echo "== validate a benchable PSpice buffer against its translation =="
"$SM" translate ../tests/fixtures/pspice_buffer.lib -o /tmp/pbuf.lib --no-banner
"$SM" validate ../tests/fixtures/pspice_buffer.lib /tmp/pbuf.lib --from pspice --ports "INP=1,OUT=0"

echo; echo "Report written to /tmp/pspice_opamp.report.md"
