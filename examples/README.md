# SpiceMeister examples

Sample vendor models and a demo you can run from the repo without installing.

| File | Dialect | Shows |
|---|---|---|
| `pspice_opamp.lib` | PSpice | `VALUE=`/`LIMIT`, `PWRS`, `TABLE`, `.FUNC` |
| `hspice_rc.sp` | HSPICE | `'expr'` quotes, `pow()`, `VOL=`/`CUR=`, `LEVEL=49` |
| `passive_rc.lib` | ngspice-native | round-trip validation baseline |

```bash
./demo.sh              # detect -> translate -> validate
../bin/smeister translate pspice_opamp.lib --profile ngspice --emit-zener
```
