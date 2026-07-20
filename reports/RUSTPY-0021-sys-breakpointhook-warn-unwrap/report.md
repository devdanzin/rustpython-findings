# RUSTPY-0021 — sys.breakpointhook() panics via warn(...).unwrap() on an unimportable $PYTHONBREAKPOINT under warnings-as-error (sys.rs:874)

**New in fusil-rustpython_09** (the `--concurrency-stress` fleet). **single-threaded**. Reliability: deterministic 5/5 (single-threaded).

## Reproducer

```python
# RUSTPY-0021 (panic, single-threaded). sys.breakpointhook() reads $PYTHONBREAKPOINT; when it
# names an unimportable module it calls warn(RuntimeWarning, ...).unwrap() at sys.rs:874. Under
# warnings-as-error the warning becomes an exception, so warn() returns Err -> .unwrap() PANICS
# and aborts the whole interpreter. CPython raises a *catchable* RuntimeWarning (no crash).
#
# Deterministic 5/5. No concurrency needed (surfaced by fusil-rustpython_09 --concurrency-stress
# because the op-mix calls sys module funcs with warnings escalated, but the bug is single-threaded).
import os
import warnings
import sys

os.environ["PYTHONBREAKPOINT"] = "nonexistent_xyz.foo"   # an unimportable module path
warnings.simplefilter("error")                            # RuntimeWarning -> exception
sys.breakpointhook()                                      # -> warn(...).unwrap() panics (sys.rs:874)
```

CPython: raises a *catchable* RuntimeWarning ('Ignoring unimportable $PYTHONBREAKPOINT: ...'); no crash.

## Root cause & fix

sys.breakpointhook() (sys.rs:849) reads $PYTHONBREAKPOINT; when it names an unimportable module, or the attribute is missing, it calls the closure print_unimportable_module_warn, which does `warn(vm.ctx.exceptions.runtime_warning, "Ignoring unimportable $PYTHONBREAKPOINT: ...", 0, vm).unwrap()` (sys.rs:867-874). warn() returns a PyResult; under warnings-as-error (warnings.simplefilter('error'), or an -W error filter) the RuntimeWarning is raised as an exception, so warn() returns Err -> `.unwrap()` PANICS and aborts the interpreter. CPython instead lets the (escalated) RuntimeWarning propagate as a normal, catchable exception. Single-threaded -- concurrency is not required; it surfaced in the --concurrency-stress fleet only because that op-mix calls sys module functions while warnings were escalated. Fix: propagate warn()'s error (`warn(...)?;`) instead of `.unwrap()`. Class = RustPython `.unwrap()` on a Python-reachable PyResult. Found in fusil-rustpython_09.
