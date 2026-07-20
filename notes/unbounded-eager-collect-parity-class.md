# Systemic class: unbounded eager-collect of an untrusted iterable (CPython type-checks, RustPython doesn't)

A RustPython-reachable function declares an argument as `Vec<PyObjectRef>` (or `ArgIterable<T>`,
`Vec<PyStrRef>`, …) and **collects the whole Python argument up front by iterating it, with no
length or type check**. CPython, at the same argument position, requires a concrete sized container
(list / tuple / sequence) and raises `TypeError`/`ValueError` *before* consuming the input. So an
**infinite or merely huge iterable** — `itertools.count()`, a generator, or an object whose
`__getitem__` never raises `IndexError` — makes RustPython balloon memory unboundedly (~0.2–1 GiB/s)
until an OOM **abort** (`memory allocation of N bytes failed`), where CPython raises a catchable
exception in O(1). No concurrency needed; a single direct call does it. RUSTPY-0012 is the exemplar.

This is distinct from the **abort-vs-`MemoryError`** class (below): there, CPython *also* collects and
*also* runs out of memory — the only difference is RustPython's uncatchable abort vs CPython's
catchable `MemoryError`. That class is not a parity gap and is much broader (every eager
iterable-consumer); it is noted at the end.

## Confirmed parity-gap instances (RustPython balloons/aborts, CPython rejects fast)

Verified under `ulimit -v 1500MB` + `timeout 12` on RustPython 0.5.0 (`heads/master:a9c2c529b`) vs
CPython 3.14.3.

| id | Rust site | Python trigger | RustPython | CPython |
|----|-----------|----------------|------------|---------|
| **RUSTPY-0012** | `stdlib/src/suggestions.rs:11` `candidates: Vec<PyObjectRef>` | `_suggestions._generate_suggestions(itertools.count(), "x")` | OOM abort (~1 GiB/s) | `TypeError: candidates must be a list` |
| **RUSTPY-0013** | `stdlib/src/lzma.rs:340` `filter_specs` (from `filters=`) | `lzma.LZMACompressor(format=lzma.FORMAT_RAW, filters=(… for _ in count()))` | OOM abort | `TypeError: object of type 'generator' has no len()` |
| **RUSTPY-0014** | `vm/src/exception_group.rs` `Constructor` (`excs`, helper at `:448`) | `ExceptionGroup("m", itertools.count())` | OOM abort (5.6 GiB / 5 s) | `TypeError: second argument (exceptions) must be a sequence` |
| **RUSTPY-0015** | `vm/src/stdlib/_ctypes/array.rs:977` (`setitem_by_slice`, collect before length check); `_ctypes/function.rs:1012/1944` (argtypes at call/callback time) | `a=(ctypes.c_int*3)(); a[0:3]=count()` ; `f=ctypes.CDLL(None).time; f.argtypes=count(); f(None)` | OOM abort | `ValueError: Can only assign sequence of same size` ; argtypes rejected at call (no balloon) |
| **RUSTPY-0016** | `vm/src/stdlib/posix.rs:1403/1337/1408/1417` — `ArgIterable<T>` argv / group_ids / setsigdef / setsigmask (collected in full, validated after) | `os.posix_spawn("/bin/true", ("/bin/true" for _ in count()), os.environ)` ; `os.setgroups(count())` ; `os.posix_spawn(..., setsigdef=count())` | OOM abort (argv) / unbounded-consume hang (the `i32`/`gid` element ones fill slower) | `TypeError: posix_spawn: argv must be a tuple or list` ; `TypeError: setgroups argument must be a sequence` ; `ValueError: signal number 0 out of range [1; 64]` |

**Common fix:** take a concrete-container type at the argument (`PyListRef` / `PyTupleRef` /
`Either<PyListRef, PyTupleRef>`), or validate length/type **before** collecting — matching CPython.
A materialised list can't be infinite, so this closes both the parity gap and the DoS.

`_ctypes` argtypes and `os.posix_spawn` `setsigdef`/`setsigmask` collect at **call/spawn time**, not
at attribute-set time (setting `f.argtypes = count()` alone is a no-op in RustPython; the balloon is
on the next call). The `ArgIterable<T>` mechanism in `posix.rs` is the same "accept any iterable,
collect it whole, validate afterward" shape — **a follow-up sweep of the other `ArgIterable`
argument sites is warranted.**

## SAFE (checked, not vulnerable — RustPython bounds or rejects too)

- **`type("X", <infinite>, {})`** — `type.__new__` requires a `tuple`; both reject fast.
- **ctypes `_fields_ = <infinite>`** — guarded by a list/tuple downcast first (`TypeError: _fields_
  must be a list or tuple`); RustPython is actually *safer* here than CPython (which balloons → ME).
- **`zip` / `map` / `itertools.zip_longest`** — `Vec<PyIter>` wraps iterators lazily; no eager collect.
- **`operator.attrgetter` / `itemgetter` / `methodcaller`** — the `Vec` is `*args` varargs, bounded.
- **`os.execv` / `os.execve` argv** — typed `Either<PyListRef, PyTupleRef>`, rejects an iterator.
- All bare `args: Vec<PyObjectRef>` in `set/int/bool/str/float/dict/tuple/descriptor/builtin_func/
  protocol::callable/vectorcall_*` — vararg collectors, bounded by the finite call.

## Related but NOT this class: abort-vs-`MemoryError` (broad, architectural)

Every eager iterable-**consumer** balloons on an infinite iterable in **both** interpreters — but
RustPython **aborts** (`handle_alloc_error`) where CPython raises a catchable `MemoryError`:
`tuple`/`list`/`set`/`frozenset`/`sorted`/`"".join(...)`/`itertools.product`/`socket.sendmsg`
buffers/`f(*count())`/`f(**{keys→count()})`. This is not a parity gap (CPython also collects); the
one difference is uncatchable abort vs catchable `MemoryError`. The real fix is global — RustPython
should raise `MemoryError` on allocation failure rather than abort. fusil's `--child-memory-limit-mb`
bounds it in the fleet (turns the balloon into a fast, clean abort) but does not fix the root.
