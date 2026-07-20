# RUSTPY-0014: ExceptionGroup / BaseExceptionGroup second (exceptions) argument
import itertools

# The (Base)ExceptionGroup constructor collects the second (exceptions) argument whole
# by iterating it; an infinite iterable balloons memory (~1.1 GiB/s) to OOM abort.
# CPython: TypeError: second argument (exceptions) must be a sequence.
ExceptionGroup('m', itertools.count())
