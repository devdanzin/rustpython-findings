# RUSTPY-0015: ctypes Array slice-assignment RHS; PyCFuncPtr.argtypes (collected at call time)
import ctypes
import itertools

# Array slice-assign collects the RHS iterable whole (extract_elements_with) BEFORE the
# length check (array.rs:977); an infinite iterable balloons to OOM abort.
# CPython: ValueError: Can only assign sequence of same size.
a = (ctypes.c_int * 3)()
a[0:3] = itertools.count()

# Second face: argtypes collected at CALL time (function.rs:1012):
#   f = ctypes.CDLL(None).time; f.argtypes = itertools.count(); f(None)  # -> OOM abort
