# RUSTPY-0007a face: genericalias::make_parameters_from_slice recurses UNGUARDED on nested
# list/tuple args (crates/vm/src/builtins/genericalias.rs:329) -> native stack overflow -> SIGSEGV.
# Found in fusil-rustpython_09 (filecmp vehicle, 3/3). A concrete site of the recursion->stack-
# overflow class (RUSTPY-0007a / upstream #2796), gdb-confirmed: many self-calls of
# make_parameters_from_slice, crashing while building a "no attribute" error at the guard page.
#
# The parameter-walk recurses whenever a generic-alias arg is a raw list/tuple. Two triggers:
#
# (1) A SELF-REFERENTIAL list/tuple arg -> infinite recursion. NOTE: this crashes CPython too
#     (its make_parameters is likewise unguarded on a self-referential container), so this exact
#     input is not a RustPython-only divergence -- it is the deterministic fleet reproducer.
import types

L = []
L.append(L)  # L = [L]
types.GenericAlias(list, (L,)).__parameters__  # walks (L,) -> recurses on L -> SIGSEGV

# (2) DEEP BOUNDED nesting shows the RustPython-specific weakness: RustPython overflows its
#     native stack at ~200k depth, where CPython still returns cleanly (both only crash by ~1M).
#     So RustPython lacks (or has a much shallower) recursion guard on this path.
#
#     import types
#     from typing import TypeVar
#     T = TypeVar("T")
#     x = (T,)
#     for _ in range(200_000):
#         x = (x,)
#     types.GenericAlias(list, (x,)).__parameters__   # RustPython: SIGSEGV; CPython: ok
#
# Fix (same as the 0007a class): a recursion-depth guard on make_parameters_from_slice, turning
# overflow into a RecursionError (CPython's Py_EnterRecursiveCall equivalent).
