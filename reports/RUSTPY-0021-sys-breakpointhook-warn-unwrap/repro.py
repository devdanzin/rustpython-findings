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
