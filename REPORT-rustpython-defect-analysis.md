# RustPython Defect Analysis — Campaign Report

*A standalone synthesis of the fusil-vs-RustPython fuzzing campaign, written to be the primary
design input for **`rustpy-review-toolkit`** (a Claude Code plugin that statically analyzes
RustPython for defects, the way `cpython-review-toolkit` analyzes CPython).*

- **Build under test:** RustPython 0.5.0 (`heads/master:a9c2c529b`, Jul 13 2026), the default
  `cargo install rustpython` binary.
- **Oracle:** CPython 3.14.3 (`~/venvs/fusil_np_verify`).
- **Findings:** 24 catalogued (`RUSTPY-0001..0024`), all in `reports/`, all with reproducers.
- **Source of truth:** `catalog/known_panics.tsv` + `reports/*/meta.json`; per-fleet narrative in
  `INDEX.md`; class notes in `notes/`.
- **Companion tool:** `tools/unwrap_scan/` — a `syn`-based static scanner that already prototypes the
  single most valuable analysis this toolkit should industrialize (Python-reachable panic sites).

---

## 1. Executive summary

Over nine fuzzing fleets we drove hostile Python at RustPython's Rust-implemented stdlib and VM and
catalogued **24 distinct ways to abort or segfault the interpreter from pure Python** where CPython
raises a normal, catchable exception (or works). The findings cluster into a small number of
**RustPython-specific classes** that a static analyzer can target directly:

| # | Class | Findings | Crash kind |
|---|-------|----------|-----------|
| A | Python-reachable `.unwrap()` / `.expect()` on a `PyResult`/`Option`/`downcast` | 0003, 0004, 0006, 0009, 0011, 0017, 0020, 0021 | panic (abort) |
| B | Unchecked indexing / missing arity check (`zelf[i]`, `args.args[N]`, `buf[i-1]`) | 0002, 0005, 0010, 0022 | panic (abort) |
| C | `unsafe` unsoundness / type confusion (a wrong pointer cast in a `Debug` impl) | 0018 | **SIGSEGV** |
| D | Unguarded native recursion on a protocol path (hash/eq/repr) → native stack overflow | 0007a | **SIGSEGV** |
| E | Uninitialized native object via `T.__new__(T)` → a protocol slot reads its payload | 0008 | **SIGSEGV** |
| F | Thread-safety: `RefCell`/`Cell`/non-atomic state on a thread-shareable object | 0001, 0019, 0020, 0022, 0023 | panic / **SIGSEGV** |
| G | Unbounded eager-collect of an untrusted iterable (parity gap: CPython type-checks first) | 0012–0016 | OOM **abort** |
| H | `ctypes`/FFI argument marshalling (accepts what CPython rejects) | 0017, 0024, 0015 | panic / **SIGSEGV** |
| I | Rust `Debug` (`{:?}`) used to format a Python object in a user-facing message | 0018 | garbage msg / **SIGSEGV** |
| J | abort-vs-`MemoryError` (Rust aborts on OOM; architectural, KNOWN upstream) | (broad) | OOM **abort** |

**The central result** (Section 2): CPython's crash corpus does **not** transfer — 0 of 138 filed
CPython crashers reproduce natively on RustPython. The two interpreters fail in **different layers**.
Everything a memory-safe Rust rewrite *buys* (no C refcount races, no UAF, no NULL-deref in
interpreter internals) it partly *gives back* as a new surface: `panic!`-family aborts, `unsafe`
soundness bugs, `RefCell` misuse for shared state, and parity gaps where Rust's "accept any iterator"
ergonomics skip a check CPython performs. A review toolkit for RustPython must be built around **these
classes**, not around CPython's (refcount/GIL/NULL) classes.

---

## 2. The central thesis: RustPython's failure surface is *orthogonal* to CPython's

RustPython is a from-scratch Python interpreter in safe-by-default Rust. Two model facts dominate
everything below:

1. **`PyRc = Arc`** — reference counts are atomic; object graphs are `Arc`/`Weak`. There is no
   non-atomic C refcount to race, and no manual `Py_INCREF`/`DECREF` to get wrong. **CPython's entire
   free-threading data-race class (non-atomic refcount/GC) is prevented by construction.** (This is
   exactly why the fusil `--tsan` mode — which found ~50 CPython FT races — is *inapplicable* to
   RustPython; see `notes/cpython-crash-corpus-vs-rustpython.md`.)
2. **Safe Rust internals** — collections are bounds-checked, there is no raw pointer arithmetic on
   object structs, and "invalid state" is usually a `Result`/`Option`, not a dangling pointer. **The
   CPython C-internal NULL-deref / OOB / C-assertion class is likewise prevented** — the hostile input
   that corrupts a CPython C struct raises a clean Rust error or just works.

What replaces them (the whole point of this report):

- **`panic!`-family aborts.** Rust's safety valve for "impossible" states is `panic!`
  (`.unwrap()`, `.expect()`, `unreachable!`, `todo!`, out-of-bounds `[]`). When a *Python-controlled*
  value reaches one of these, a would-be `TypeError`/`ValueError` becomes an **interpreter abort**.
  This is the single largest class (A+B: 12 of 24 findings). A panic is "safe" (no UB) but still a
  hard crash of the whole process — and on a worker thread it can poison state and hang.
- **`unsafe` islands.** RustPython has `unsafe` where it bridges the object model, FFI, and atomics.
  Bugs there are real memory-unsafety (SIGSEGV), not panics. `RUSTPY-0018` (an `unsafe` `Debug` impl
  casting `Py<T>` to `T`) is the exemplar and the highest-severity single finding.
- **The wrong synchronization primitive.** Python objects are shareable across threads, but several
  are built on `RefCell`/`Cell` (single-threaded interior mutability). Under free-threading
  (`PYTHON_GIL=0`), concurrent access trips `RefCell`'s runtime borrow check → `BorrowMutError`
  **panic**, or races a non-atomic index → OOB **panic**, or corrupts VM frame state → **SIGSEGV**.
  (Class F: 0001, 0019, 0020, 0022, 0023.)
- **Ergonomic parity gaps.** Rust's `impl IntoIterator` ergonomics make "accept any iterable and
  collect it" the path of least resistance. CPython, at many argument positions, requires a *sized*
  container and type-checks first. RustPython collecting an infinite iterable → unbounded memory →
  OOM **abort**. (Class G: 0012–0016.)
- **FFI (`ctypes`).** The one place RustPython is *also* memory-unsafe by nature. Its argument
  marshalling accepts values CPython rejects (`RUSTPY-0024`: a `float` marshalled to a pointer →
  SIGSEGV where CPython raises `ArgumentError`).

**Design consequence for the toolkit:** do not port `cpython-review-toolkit`'s agents 1:1. A
"refcount-auditor" or "GIL-discipline-checker" has almost nothing to do on RustPython. The high-value
agents are *panic-site*, *unsafe-soundness*, *thread-safety (RefCell-for-shared-state)*,
*eager-collect-parity*, *recursion-guard*, *uninitialized-object*, *debug-format*, and *ctypes-FFI* —
each mapped to a concrete Rust idiom in Section 5, and each grounded in a real finding.

---

## 3. How the campaign was run (methodology)

Understanding where findings came from tells the toolkit designer which classes a *static* pass can
catch cheaply and which need a differential/dynamic step.

### 3.1 The fuzzer
`fusil` (a generational fuzzer) with a `--rustpython` plugin generates a Python script per session
that imports a target module and hammers its functions/classes/methods with **hostile arguments**:
wrong types, wrong arity, "bomb" objects whose dunders raise or return wrong-typed values, recursive
object graphs, huge ints, surrogates, and (crucially) **wrong-type-return** objects (RustPython often
`.unwrap()`s a `downcast`, so an object returning the wrong type from a dunder trips it). A crash =
the child aborts/segfaults; the session dir is kept and labelled.

### 3.2 The four generation modes (each mined a different class)
- **Default** — per-call hostile args across all discovered modules. Found the panic/index classes
  (0001–0011).
- **`--modules-file rust_modules.txt`** — restrict to the ~108 **Rust-implemented** modules (from
  `unwrap_scan`), where the crash density is. This is *static-guided targeting* and is the direct
  ancestor of what this toolkit should do.
- **`--new-uninit`** — call `T.__new__(T)` on every native type (bypassing `__init__`) then poke its
  protocol slots. Found the uninitialized-object segv class (0008). *Still under-mined — the remaining
  unmined variant.*
- **`--concurrency-stress`** — a barrier-released multi-thread op-mix (attribute churn, gc, weakref,
  shared-container mutation, **shared-iterator advance**, read-while-mutate) over a few shared objects.
  This is the free-threading analogue and found the entire thread-safety class (0019, 0020, 0022,
  0023) plus, via its ctypes objects, 0024. **Most productive variant once the primary mode converged.**

### 3.3 Fleets, dedup, and convergence
Runs are fleets of parallel instances. An in-loop deduper (`fusil/python/rustpython_dedup.py`) parses
each crash's stdout into a **panic signature** — `crates/<path>.rs:<line>` (column dropped, absolute
paths normalized) — and labels the dir `RUSTPY-00NN` (known) / `rustpyNEW` (new site) / `rustpySEGV`
(no panic message → needs gdb). A read-only snapshot (`catalog/known_panics.tsv`) drives it. **Key
property:** the panic signature is the dedup key, so the panic *classes* (A/B/F-panic) dedup cleanly,
but **segfaults have no panic line** and fall into an undifferentiated `rustpySEGV` bucket — which is
where the two highest-value findings (0018, 0024) hid until a **gdb-resolve pass**. (Lesson in §6.)

Fleets 07–08 converged (primary mode mined out: ~14k sessions, 0 new). The variant surfaces did not:
fleet 09 (`--concurrency-stress`) produced 6 new findings. **Thesis: the productive surface is the
*mode*, not more sessions.** A static toolkit inverts this — it can enumerate the whole surface at
once instead of waiting for a fuzzer to stumble onto each site.

### 3.4 Verification (the differential oracle)
Every finding is confirmed by running the *same* reproducer under CPython 3.14.3 and recording that
CPython raises/handles it. This CPython-divergence check is the crux of "is it a bug?" — and it is the
single most important thing the toolkit must automate (Section 5, `cpython-parity-differential`).

### 3.5 The static tool that already exists
`tools/unwrap_scan/` (Rust, `syn`-based) walks `crates/vm/src/stdlib`, `crates/stdlib/src`,
`crates/vm/src/builtins`, `crates/vm/src/types`; attributes every `.unwrap()`/`.expect(`/`panic!`/
`unreachable!`/`unimplemented!`/`todo!`/`.args[` to the function it lives in; and classifies each by
**Python-reachability**: `py` (directly `#[pyfunction]`/`#[pymethod]`/`#[pygetset]`/`#[pyslot]`),
`protocol` (a slot of a `with(Trait)` protocol impl), or `internal` (a helper reached transitively).
Current surface: **973 risky lines** across 108 modules (686 internal, 159 `py`, 126 `protocol`, +1).
This is the seed of the toolkit's flagship agent — see §5.1 and §7.

---

## 4. The bug taxonomy (classes & patterns)

Each class below gives the **mechanism**, the **static-detection signature** (the Rust idiom an
analyzer keys on), the **CPython contrast** (the oracle), the **findings**, and the **fix pattern**.
Findings can belong to more than one class (noted).

### Class A — Python-reachable `.unwrap()` / `.expect()` on a `PyResult`/`Option`/`downcast`
**Mechanism.** A native function computes a `Result`/`Option` whose `Err`/`None` case is
*Python-reachable* (a raising dunder, an unregistered key, an out-of-range int, an escalated warning)
and calls `.unwrap()`/`.expect()` instead of propagating with `?`. The `Err`/`None` becomes a panic →
abort.
**Static signature.** `.unwrap()` / `.expect(` on an expression of type `PyResult<_>` or
`Option<_>` inside a `py`/`protocol`-reachable function, *especially* right after a call that runs
Python (`.repr(vm)`, `.str(vm)`, `vm.call_method`, `.downcast`, a `warn(...)`, a hashmap `.get`). The
tell is that the fallible value is derived from a Python object or Python-controlled input.
**CPython contrast.** CPython propagates the inner exception (`repr` that raises) or raises
`TypeError`/`ValueError`/`KeyError`.
**Findings.**
- **0003** `class.rs:87` — `unwrap_or_else(|| fail(...))` on an uninitialized static type panics
  ("static type has not been initialized"). Repro `import _md5; _md5.md5()`. *(KNOWN upstream #5210.)*
- **0004** `csv.rs:805` (+`:748/:1070`) — `get_lineterminator` does a `HASHMAP…get(name).unwrap()` on
  an unregistered dialect. Repro `import _csv; _csv.reader([]).__next__()`.
- **0006** `builtins.rs:557/607` — `compile`/`eval` `expect_str` panics on a surrogate. Repro
  `eval(chr(0xd800))`.
- **0009** `staticmethod.rs:182` — `repr(staticmethod(obj))` does `obj.repr(vm).unwrap()`; a raising
  `__repr__` panics. **0011** `classmethod.rs:198` is the exact sibling one type over. *(File as one
  `.repr(vm).unwrap()` fix.)*
- **0017** `_ctypes/simple.rs:908` (+`:895/921/759/775/791/807`) — every ctypes simple int/pointer
  type does `.to_usize()/.to_i128().expect("int too large")`. Repro `ctypes.c_char_p(2**64)`. CPython
  *masks* to the C width. (Also Class H.)
- **0020** `utils.rs:61` — `collection_repr` does `iter.next()…expect("this is not called for empty
  collection")` after a non-empty check; a concurrent empty makes `next()` → `None` → panic. (Also
  Class F.)
- **0021** `sys.rs:874` — `sys.breakpointhook()` on an unimportable `$PYTHONBREAKPOINT` calls
  `warn(RuntimeWarning, …).unwrap()`; under warnings-as-error `warn()` returns `Err` → panic.
**Fix pattern.** Replace `.unwrap()/.expect()` with `?` (propagate), or map to a proper
`vm.new_*_error(...)`. For the `.repr(vm).unwrap()` idiom: `obj.repr(vm)?`.

### Class B — Unchecked indexing / missing arity check
**Mechanism.** A native function indexes a slice/struct-seq/args tuple with an index it didn't
bounds-check, on a Python-controllable length. `zelf[i]`, `args.args[N]`, `buf[in_idx - 1]` with
`in_idx == 0` (usize underflow → astronomically OOB).
**Static signature.** Direct `[` indexing (not `.get(i)`) on `args.args`, a struct-seq buffer, or a
`Vec` whose length is Python-controlled, in a `py`/`protocol` function — plus any `x - 1` used as an
index without an `x > 0` guard. (`unwrap_scan` flags `.args[` explicitly; a fuller AST pass should
flag all index-expressions in reachable fns.)
**CPython contrast.** `TypeError` (wrong arity) / `IndexError` / a correct empty-case result.
**Findings.** **0002** `structseq.rs:311` (`pwd.struct_passwd().pw_name` on an empty struct-seq);
**0005** `_typing.rs:43` (`_typing._idfunc()` indexes `args.args[0]` with no arity check); **0010**
`binascii.rs:507` (`b2a_qp(b'\n')` → `buf[in_idx-1]` underflow); **0022** `itertools.rs:282` (cycle
cache OOB — non-atomic, so also Class F).
**Fix pattern.** `.get(i)` + explicit error; arity-check `args` before indexing; guard `i > 0` before
`i - 1`.

### Class C — `unsafe` unsoundness / type confusion  *(highest severity)*
**Mechanism.** An `unsafe` block makes an invalid assumption about memory layout. `RUSTPY-0018`:
`PyAtomicRef<T>`'s `Debug` impl does `self.inner.load().cast::<T>().as_ref().fmt(f)` — but `inner`
points at a **`Py<T>`** (object header + payload), and *every other method* of the type casts to
`Py<T>`. The `Debug` impl alone casts to bare `T`, skipping the header, so it reads misaligned fields;
for `PyFunction.code: PyAtomicRef<PyCode>`, `CodeObject::Debug` then dereferences garbage heap
pointers → **SIGSEGV**.
**Static signature.** `unsafe` blocks that `.cast::<_>()` / `transmute` / `as *const/*mut` an object
pointer; **inconsistency** between how one method interprets a stored pointer and how its siblings do
(here `Deref`/`load_raw`/`From`/`swap` all use `Py<T>`, `Debug` uses `T`). Also: any `unsafe` `Debug`
impl at all is a smell (Debug should never be `unsafe`).
**CPython contrast.** N/A (this is a Rust-internal soundness bug); functionally CPython prints a
bounded repr.
**Fix pattern.** Make the cast consistent (`.cast::<T>()` → `.cast::<Py<T>>()`); prefer a safe
accessor.

### Class D — Unguarded native recursion → native stack overflow
**Mechanism.** A protocol operation (hash, richcompare, repr, `genericalias` parameter walk) recurses
in Rust following a Python object graph with **no recursion depth guard**; a deep/cyclic object
overflows the *native* stack → SIGSEGV (not a catchable `RecursionError`).
**Static signature.** A `#[pymethod]`/protocol impl for `__hash__`/`__eq__`/`__repr__`/`__reduce__`/
parameter-collection that calls back into the same operation on a contained object **without**
entering a recursion guard (CPython uses `Py_EnterRecursiveCall`; RustPython has
`vm.with_recursion(...)`-style helpers that these paths *omit*).
**CPython contrast.** `RecursionError`.
**Findings.** **0007a** (umbrella; KNOWN upstream **#2796**). Per-area fixes have landed (json, AST,
parser) but hash/compare/genericalias remain.
**Fix pattern.** Wrap the recursive descent in the VM recursion guard.

### Class E — Uninitialized native object via `T.__new__(T)` → protocol slot reads payload
**Mechanism.** Most native types have no own `Constructor`/`DISALLOW_INSTANTIATION`, so `T.__new__(T)`
yields a type-confused instance with a default (`PyBaseObject`) payload. Ordinary `#[pymethod]`s
cleanly `downcast`-fail (`TypeError`), but a **protocol slot that touches the payload without
re-downcasting** reads garbage → SIGSEGV. `RUSTPY-0008`: `re.Match`'s `AsMapping` subscript reads
uninitialized `regs`/`string`.
**Static signature.** A type whose `AsMapping`/`AsSequence`/`AsNumber`/iterator slot accesses
`zelf.payload`/fields **without** the guard its `#[pymethod]`s use, *and* the type has no
`Constructor` that forbids `__new__`. (Cross-reference: types missing
`impl Constructor`/`DISALLOW_INSTANTIATION` whose slots read payload.)
**CPython contrast.** Segfault-safe (CPython's slots re-check or the type forbids `__new__`).
**Findings.** **0008** (`_sre` `Match::as_mapping`). Minimized: `M=type(re.match('a','a')); M.__new__(M)[0]`.
**Fix pattern.** Add the payload/init guard to the slot (as `group()`/`repr` already have), or forbid
bare `__new__`.

### Class F — Thread-safety: the wrong primitive for a shared object
**Mechanism.** A Python object reachable across threads holds state in `RefCell`/`Cell` (single-thread
interior mutability) or advances a non-atomic index, or the VM's frame/coroutine machinery has a
race. Under `PYTHON_GIL=0`:
- `RefCell` concurrent borrow → **`BorrowMutError` panic**. **0001** (`_thread._local`
  `LOCAL_GUARDS`), **0019** (`contextvars` `RefCell<Hamt>`). These are *the same class* — `RefCell`
  used for a thread-shareable Python object. 0001 also has a **re-entrancy** flavour (a `__del__`
  re-enters the `RefCell` while a borrow is held), which is the one CPython-corpus shape worth
  hand-porting.
- **TOCTOU on shared state.** **0020** (`collection_repr`: non-empty check then `next().expect()`,
  raced by a concurrent `clear`), **0022** (`itertools.cycle`: `saved[index.fetch_add(1)]` with a
  *separate* non-atomic reset → OOB when two threads race past the wrap).
- **VM machinery race.** **0023** (concurrent generator resume underflows the frame value stack →
  `frame.rs:10092 fatal "tried to pop from empty stack"` → SIGSEGV). The `Coro` `running` CAS guards
  *simultaneous* resume but `send`/`send_none` read `frame.lasti()` before it; the exact window is a
  sub-1% race (measured 3/500 on the vehicle, 0/1400 on synthetics — see §6).
**Static signature.** `RefCell<_>`/`Cell<_>`/`AtomicCell<_>` **fields on a `#[pyclass]` type that is
`Send + Sync`** (i.e., not `#[pyclass(unsendable)]`) — those objects can be shared, so a `!Sync` cell
is a latent double-borrow. Also: `fetch_add`/`load`+`store` sequences that implement a
read-modify-write in *separate* atomic ops (non-atomic RMW → race). Also: a non-empty/emptiness check
followed by an `.expect()`/`[0]` on the same shared container (TOCTOU).
**CPython contrast.** Thread-safe (GIL, or FT-guarded); raises `RuntimeError`
("changed size during iteration") / `ValueError` ("generator already executing") at worst.
**Fix pattern.** `Mutex`/`RwLock`/atomic Hamt instead of `RefCell`; single-atomic RMW (`(i+1)%len`
via CAS); re-check after the borrow; recompute rather than trust a prior length. And: **never run
Python (`Drop`→`__del__`) while holding a `RefCell` borrow** (the 0001 re-entrancy rule).

### Class G — Unbounded eager-collect of an untrusted iterable (parity gap)
**Mechanism.** A native function takes `Vec<PyObjectRef>`/`ArgIterable<T>`/`Vec<PyStrRef>` and
**collects the whole Python argument up front** with no length/type check. CPython, at the *same*
position, requires a concrete sized container and raises `TypeError`/`ValueError` **before** consuming
it. So an infinite/huge iterable (`itertools.count()`, a generator, a lying `__getitem__`) makes
RustPython balloon (~0.2–1 GiB/s) → OOM **abort**, where CPython rejects in O(1). *No concurrency
needed; a single direct call does it.*
**Static signature.** A `#[pyfunction]`/`#[pymethod]`/`Constructor` argument typed `Vec<PyObjectRef>`
/ `ArgIterable<_>` / `Vec<PyRef<_>>` that is `.collect()`ed / iterated **before** a length or
element-type validation — cross-referenced against CPython's signature for that call (does CPython
require a `list`/`tuple`/sequence there?). Note the *negative* set is large and important:
`*args` varargs (bounded by the finite call), lazily-wrapped `Vec<PyIter>` (zip/map), and
`Either<PyListRef, PyTupleRef>` (already rejects iterators) are **safe** — see
`notes/unbounded-eager-collect-parity-class.md` for the full SAFE list.
**CPython contrast.** `TypeError: … must be a list/sequence` / `ValueError` before consuming.
**Findings.** **0012** `suggestions.rs:11` (`_suggestions._generate_suggestions(count(), "x")`),
**0013** `lzma.rs:340` (`filters=`), **0014** `exception_group.rs` (`ExceptionGroup("m", count())`),
**0015** `_ctypes/array.rs:977` + `function.rs` (slice-assign / argtypes; also Class H), **0016**
`posix.rs` (`posix_spawn` argv / `setgroups` / `setsigdef`).
**Fix pattern.** Take a concrete container type (`PyListRef`/`Either<PyListRef,PyTupleRef>`) or
validate length/type before collecting. (Distinct from Class J — here CPython *rejects*, not just
"catchably runs out of memory".)

### Class H — `ctypes` / FFI argument marshalling
**Mechanism.** `ctypes` is the one place RustPython is inherently memory-unsafe (it calls real C via
libffi). Two sub-bugs found: **0017** — simple-type constructors `.expect("int too large")` on huge
ints (Class A panic; CPython masks to width); **0024** — the no-`argtypes` argument converter
(`conv_param`, `_ctypes/function.rs:182`) has a `float → f64` branch **CPython has no equivalent of**,
and the FFI dispatch then treats it as a pointer → **SIGSEGV**, where CPython raises `ArgumentError`
("Don't know how to convert parameter 1"). (`ctypes.CDLL("libc.so.6").strlen(1.5)`.)
**Static signature.** In `_ctypes/*.rs`: argument-conversion branches (`conv_param`,
`convert_to_pointer`) that accept a Python type CPython's converter rejects; `.expect()`/`.unwrap()`
on int→C-width conversions; eager `.collect()` of `argtypes`/array-slice (Class G). Compare against
CPython `Modules/_ctypes` conversion rules.
**CPython contrast.** `ArgumentError` / width-masking / bounds-check.
**Fix pattern.** Match CPython's converter acceptance set (drop the float branch; raise "Don't know
how to convert"); mask instead of `.expect()`.
**Caveat.** ctypes crashes need triage: passing a *small int* as a pointer segfaults in **both**
interpreters (generic "int as pointer") — that's *not* a bug. Only a *divergence* (CPython raises,
RustPython crashes) counts. This is a recurring toolkit gotcha (§6).

### Class I — Rust `Debug` (`{:?}`) used for a user-facing message
**Mechanism.** A native error message formats a Python object with `{:?}` (Rust `Debug`) instead of
the Python `repr`. Two harms: (1) the message is a multi-KB dump of the object's internal Rust struct;
(2) it can reach the unsound `PyAtomicRef` `Debug` (Class C) → SIGSEGV. `RUSTPY-0018`'s *trigger* is
`_asyncio._enter_task`'s `format!("Cannot enter into task {:?} …", task, current_task)`.
**Static signature.** `format!`/`write!`/`vm.new_*_error(format!(...))` where a `{:?}` (or `{:#?}`)
formats a `PyObjectRef`/`PyRef<_>`/`Py<_>`. Grep-able; high-precision. (Also flag `{:?}` on any type
that transitively contains a `PyAtomicRef`.)
**CPython contrast.** `%R` (Python `repr`) — bounded and safe.
**Fix pattern.** `obj.repr(vm)?` in the message.

### Class J — abort-vs-`MemoryError` (architectural; KNOWN & OPEN upstream)
**Mechanism.** Every eager iterable *consumer* balloons on an infinite iterable in **both**
interpreters; RustPython **aborts** (`handle_alloc_error`) where CPython raises a catchable
`MemoryError`. Not a parity gap (CPython also collects). **KNOWN OPEN upstream: RustPython #3493
("MemoryError/fallible allocations"), #1779.** It persists because it's pervasive (needs
`try_reserve`/fallible allocation threaded everywhere), not overlooked.
**Toolkit stance.** **Do not report instances as findings.** A scanner *may* tag allocation-heavy
sites for the #3493 effort, but every `sched`/`multiprocessing`/`statistics` OOM-abort we saw is this
class. (28 of fleet-09's 49 segv-bucket dirs were exactly this.)

---

## 5. Advice for `rustpy-review-toolkit` design

The toolkit should be a **panel of static-analysis agents** (mirroring `cpython-review-toolkit`'s
structure — a scanner script + an LLM triage agent per dimension), but keyed to the Rust/RustPython
idioms above. Below: the agents to build, in priority order, each with its scan target, triage
heuristics, and the finding(s) that validate it.

### 5.0 Foundational: an orientation/mapper agent (run first)
Analogous to `cpython-review-toolkit`'s `include-graph-mapper`. It must teach the other agents
**RustPython's macro object model**, because Python-name attribution is non-trivial:
- **`#[pymodule] mod foo`** → module `foo`; **`#[pyclass] impl/struct`** → a type; **`#[pyfunction]`
  / `#[pymethod]` / `#[pygetset]` / `#[pyslot]` / `#[pystaticmethod]` / `#[pyclassmethod]`** → exposed
  callables. The Python name is the attr's `name = "..."` override, else the Rust ident;
  `#[pymethod(magic)]` → `__ident__`.
- **`#[pyclass(with(Trait, …))]`** wires *protocol* impls (`Representable`, `AsMapping`,
  `AsSequence`, `AsNumber`, `Iterable`, `Constructor`, `Hashable`, `Comparable`, …) — these are
  Python-reachable via the protocol (repr/subscript/iter/hash/…) even though they aren't
  `#[pymethod]`s. **0008 and 0009 live here** — a toolkit that only looks at `#[pymethod]` misses
  them. The name→trait mapping is defined in `crates/derive-impl/src/{pymodule,pyclass,util}.rs`;
  pre-index all `impl Trait for X` blocks once so a `protocol` site resolves to `X`'s Python protocol.
- **Reachability tiers** (from `unwrap_scan`): `py` (direct attr) > `protocol` (slot) > `internal`
  (transitive helper). Triage priority follows this order; `internal` is real but "one call away".
- **What NOT to build:** no refcount-auditor, no GIL-lock-pairing-auditor, no NULL-deref-in-internals
  auditor. `PyRc = Arc` + safe Rust make those empty. (Confirmed: 0/138 CPython crashers transfer.)

### 5.1 `panic-site-auditor` — the flagship (Classes A + B; validates 0002–0006, 0009–0011, 0017, 0020, 0021)
**This is the single highest-value agent.** Industrialize `unwrap_scan`:
- **Scan:** `.unwrap()`, `.expect(`, `panic!`, `unreachable!`, `unimplemented!`, `todo!`, and **all
  index expressions** (`x[i]`, especially `args.args[N]` and any `i - 1` index) in `py`/`protocol`
  functions of `crates/vm/src/stdlib`, `crates/stdlib/src`, `crates/vm/src/builtins`,
  `crates/vm/src/types` (and `_ctypes`).
- **Triage (the LLM step):** the 973-site raw surface is mostly noise — `.unwrap()` is idiomatic on
  *statically-known* invariants. The signal is **Python-reachability of the `Err`/`None`/OOB case**.
  Rank a site UP when the fallible value derives from: a Python argument, a `.repr(vm)`/`.str(vm)`/
  `vm.call_method` result, a `downcast`, a user-registered map `.get`, an int→width conversion, a
  `warn(...)`, or an index whose bound is a Python-controlled length. Rank DOWN when it's on a
  compile-time constant, a just-inserted map key, or a value the function itself just validated.
- **Differential confirm:** for each high-ranked site, synthesize the Python call that reaches it and
  diff CPython (§5.9).
- **Yield estimate:** 159 `py` + 126 `protocol` sites; the campaign converted ~12 of these to
  findings by *fuzzing*. A guided static+differential pass should convert many more.

### 5.2 `unsafe-soundness-auditor` (Class C; validates 0018) — highest severity
- **Scan:** every `unsafe` block in `crates/vm/src/object/`, `crates/vm/src/builtins/`, and anywhere
  touching `Py<T>`/`PyObject`/`PyAtomicRef`/payload. Flag pointer `.cast::<_>()`, `transmute`,
  `as *const/*mut`, and **`unsafe impl Debug`**.
- **Triage:** the killer heuristic is **cross-method inconsistency** — when one method interprets a
  stored pointer as `Py<T>` and a sibling interprets it as `T` (0018 exactly). Also: any `Debug` that
  is `unsafe`; any pointer cast whose target type differs from the type the `From`/constructor stored.
- **Note:** this class is small but each instance is a true SIGSEGV. Worth a deep, precise pass.

### 5.3 `thread-safety-auditor` (Class F; validates 0001, 0019, 0020, 0022, 0023)
- **Scan:** `RefCell<_>`/`Cell<_>` **fields on `#[pyclass]` types that are NOT `unsendable`**
  (shareable objects with a `!Sync` cell); non-atomic read-modify-write via separate atomic ops
  (`fetch_add` then a conditional `store`); emptiness/length checks followed by `.expect()`/`[0]`/
  `next()` on the same shared container; and `Drop`/`__del__`-reachable code that runs while a
  `RefCell` borrow is held (0001).
- **Triage:** "can two threads reach this object's mutable state at once?" For a `#[pyclass]` that
  isn't `unsendable`/frozen: yes. Rank the `RefCell` fields highest (0001/0019 are guaranteed
  BorrowMutErrors under contention). Non-atomic RMW and TOCTOU need the shared-across-threads argument.
- **Confirm:** these need a *concurrency* differential (spawn threads), not a single-shot diff.

### 5.4 `eager-collect-parity-auditor` (Class G; validates 0012–0016)
- **Scan:** `#[pyfunction]`/`#[pymethod]`/`Constructor` params typed `Vec<PyObjectRef>` /
  `ArgIterable<_>` / `Vec<PyRef<_>>` that are collected/iterated before validation.
- **Triage:** this agent **must** consult CPython's signature for the same call — the bug only exists
  where CPython requires a *sized container* at that position. Maintain (or fetch) CPython's argument
  spec. Filter the large SAFE set (varargs, lazy `Vec<PyIter>`, `Either<PyListRef,PyTupleRef>`).
- **Confirm:** differential with an infinite iterable under a memory cap (`ulimit -v`): RustPython
  aborts, CPython raises fast.

### 5.5 `recursion-guard-auditor` (Class D; validates 0007a)
- **Scan:** protocol impls for `__hash__`/`__eq__`/`__repr__`/`__reduce__`/genericalias parameter
  walks that recurse into contained objects **without** a `vm.with_recursion`-style guard.
- **Triage:** compare against the paths that *do* guard (json/AST already fixed); flag the unguarded
  hash/compare/genericalias descents. Known umbrella #2796 — still worth enumerating the remaining
  sites precisely.

### 5.6 `uninitialized-object-auditor` (Class E; validates 0008)
- **Scan:** types **without** an `impl Constructor` that forbids `__new__`
  (`DISALLOW_INSTANTIATION`) whose protocol slots (`AsMapping`/`AsSequence`/`AsNumber`/iterator) read
  `zelf` payload/fields **without** the guard the type's `#[pymethod]`s use.
- **Triage:** the vulnerable shape is "slot touches payload, `__new__` allowed, no re-check". Enumerate
  native types and diff their slot bodies against their method bodies for the missing guard.

### 5.7 `debug-format-auditor` (Class I; validates 0018-trigger) — cheap & high-precision
- **Scan:** `format!`/`write!`/`vm.new_*_error(format!(...))` with a `{:?}`/`{:#?}` whose argument is
  a `PyObjectRef`/`PyRef<_>`/`Py<_>` (or a struct transitively containing a `PyAtomicRef`).
- **Triage:** nearly all true positives — user-facing messages should use `repr`, not `Debug`. Grep
  `_asyncio.rs`, `typevar.rs`, `os.rs` first (known instances: `_asyncio.rs:2492`, `typevar.rs:933/996`,
  `os.rs:946/1069`). Low effort, immediate value.

### 5.8 `ctypes-ffi-auditor` (Class H; validates 0017, 0024)
- **Scan:** `_ctypes/*.rs` argument converters (`conv_param`, `convert_to_pointer`), int→C-width
  `.expect()`/`.unwrap()`, and `argtypes`/array-slice eager collects.
- **Triage:** diff the acceptance set against CPython's `Modules/_ctypes` converter (0024 = a float
  branch CPython lacks). Apply the **small-int-as-pointer** filter (crashes both → not a finding).

### 5.9 `cpython-parity-differential` — the cross-cutting oracle (used by every agent)
The one piece that turns "a panic exists" into "a *bug* exists". For a candidate site, synthesize the
Python that reaches it and run it under **both** RustPython and CPython 3.14+, classifying:
`RustPython crash + CPython raises/works` = **finding**; `both crash` = not a finding (e.g.
int-as-pointer, huge-int OOM); `both work` = false positive. This is exactly the campaign's
verification step and should be a reusable harness (guarded: `ulimit -v`, `timeout`, `setarch -R`,
`PYTHON_GIL=0` for FT/concurrency checks — see §6). It is what elevates the panic-site agent from "285
suspicious lines" to "N confirmed divergences".

### 5.10 What to *filter* as noise (don't waste triage on these)
- The huge `internal`-tier `.unwrap()` surface where the `Err` case is a real invariant.
- abort-vs-`MemoryError` (Class J) — architectural, known #3493/#1779.
- `both crash` differentials (int-as-pointer, huge-int → OOM in both).
- Subinterpreter-specific issues (out of scope by policy, cf. CPython #143232 for the FT-subinterp line).
- Anything already fixed after `a9c2c529b` — pin the analyzed checkout and record provenance (as
  `unwrap_scan` does in its TSV header).

---

## 6. Gotchas & lessons (fuzzing, triage, and analysis)

These are the traps that cost time; the toolkit should encode them.

1. **The `rustpySEGV` bucket needs a gdb-resolve pass — segfaults have no dedup signature.** The two
   highest-value findings (0018, 0024) hid in an undifferentiated segv bucket because panic-site dedup
   can't see inside a segfault. A static toolkit sidesteps this (it reads source, not stdout), but any
   *dynamic* confirmation step must gdb-resolve segvs to a frame, not lump them.
2. **Single-thread-reduce concurrency crashes before assuming it's a race.** 0024 surfaced only in the
   `--concurrency-stress` fleet, but the root cause is single-threaded (a `float` arg). Always try to
   strip concurrency; many "concurrency" crashes have a simpler core.
3. **Small-int-as-pointer segfaults in *both* interpreters — not a bug.** ctypes lets you pass an int
   as a pointer everywhere; only a *divergence* counts. Build the both-crash filter into the ctypes/FFI
   oracle.
4. **`{:?}` Debug is a landmine because of `PyAtomicRef`.** A "harmless" debug-format in an error
   message reached an *unsound* `Debug` impl and segfaulted (0018). Treat `{:?}`-on-PyObject as
   potentially fatal, not cosmetic.
5. **Beware generic fatal sinks when deduping.** `frame.rs:10092` (`ExecutingFrame::fatal`, "pop from
   empty stack") is where *many* unrelated corruptions funnel; we deliberately excluded it from the
   dedup catalog to avoid over-matching. Static analysis should likewise treat such sinks as symptoms,
   not root-cause identities.
6. **Rare races: measure a baseline, don't over-invest.** 0023 reproduces 3/500 (~0.6%) on its exact
   vehicle and 0/1400 across seven synthetic shapes. Some VM-machinery races are real but sub-1% and
   have no minimal repro; record the measured rate and move on rather than chasing a synthetic.
7. **CPython's crash corpus is worthless as a seed (0/138).** Don't design the toolkit around CPython's
   crash patterns. The one shape worth hand-porting is FT re-entrancy (`__del__` re-entering
   thread-locals → the 0001 `RefCell` class).
8. **Memory balloons need a hard cap.** RustPython's `Arc` model plus runaway threads plus the Class G
   eager-collects make OOM-abort the *loudest* (and least interesting) signal. The fuzzer needed a real
   `RLIMIT_AS` cap to keep the balloon from filling swap; a differential harness needs `ulimit -v` too.
9. **Dedup catalog staleness re-flags known bugs as NEW.** Fleet 09 re-flagged 0019/0020 as `rustpyNEW`
   because it ran an older snapshot. If the toolkit keeps a "known findings" list, version it against
   the analyzed checkout.
10. **Free-threading env matters.** Concurrency findings need `PYTHON_GIL=0`; the FT build also wants
    `setarch -R` (disable ASLR for stability) and unlimited `RLIMIT_AS` lifted only under a watchdog.
11. **Name attribution is the hard part of static analysis here.** A risky line's owning Python name
    lives behind the `derive-impl` macro system (module/class/method attrs, `with(Trait)` protocol
    wiring, `name=` overrides, `magic` → dunder). Get this right first (§5.0) or every agent
    mis-attributes.

---

## 7. Appendix

### 7.1 Full findings index (all 24)
| id | class | kind | site | one-line reproducer | CPython |
|----|-------|------|------|---------------------|---------|
| 0001 | F | panic | `_thread.rs:977` | global `_local` + stored value whose `__del__` re-enters `_local`, across threads | thread-safe |
| 0002 | B | panic | `structseq.rs:311` | `import pwd; pwd.struct_passwd().pw_name` | `TypeError` |
| 0003 | A | panic | `class.rs:87` | `import _md5; _md5.md5()` | works (#5210) |
| 0004 | A | panic | `csv.rs:805` | `import _csv; _csv.reader([]).__next__()` | works |
| 0005 | B | panic | `_typing.rs:43` | `import _typing; _typing._idfunc()` | `TypeError` |
| 0006 | A | panic | `builtins.rs:557` | `eval(chr(0xd800))` | `UnicodeEncodeError` |
| 0007a | D | segv | recursion (hash/eq/repr) | deep/cyclic object hashed/compared | `RecursionError` (#2796) |
| 0008 | E | segv | `_sre` `Match::as_mapping` | `M=type(re.match('a','a')); M.__new__(M)[0]` | safe |
| 0009 | A | panic | `staticmethod.rs:182` | `repr(staticmethod(o))` where `o.__repr__` raises | propagates |
| 0010 | B | panic | `binascii.rs:507` | `import binascii; binascii.b2a_qp(b'\n')` | returns `b'\n'` |
| 0011 | A | panic | `classmethod.rs:198` | `repr(classmethod(o))` where `o.__repr__` raises | propagates |
| 0012 | G | abort | `suggestions.rs:11` | `_suggestions._generate_suggestions(itertools.count(),'x')` | `TypeError` |
| 0013 | G | abort | `lzma.rs:340` | `lzma.LZMACompressor(FORMAT_RAW, filters=(… for _ in count()))` | `TypeError` |
| 0014 | G | abort | `exception_group.rs` | `ExceptionGroup('m', itertools.count())` | `TypeError` |
| 0015 | G/H | abort | `_ctypes/array.rs:977` | `a=(ctypes.c_int*3)(); a[0:3]=itertools.count()` | `ValueError` |
| 0016 | G | abort | `posix.rs:1403` | `os.posix_spawn('/bin/true', ('/bin/true' for _ in count()), os.environ)` | `TypeError` |
| 0017 | A/H | panic | `_ctypes/simple.rs:908` | `import ctypes; ctypes.c_char_p(2**64)` | masks to width |
| 0018 | C/I | **segv** | `ext.rs:272` (via `_asyncio.rs:2492`) | `import _asyncio\ndef f():pass\n_asyncio._enter_task(0,f)\n_asyncio._enter_task(0,f)` | `RuntimeError` |
| 0019 | F | panic | `contextvars.rs:82` | shared `contextvars.Context`/`ContextVar` across threads | thread-safe |
| 0020 | A/F | panic | `utils.rs:61` | concurrently `clear()` + `repr()` a shared set/dict | `RuntimeError` |
| 0021 | A | panic | `sys.rs:874` | `PYTHONBREAKPOINT=nonexistent.foo` + `warnings.simplefilter('error')`; `sys.breakpointhook()` | catchable `RuntimeWarning` |
| 0022 | B/F | panic | `itertools.rs:282` | `c=itertools.cycle([0]); next(c)`; 8 threads `next(c)` | never crashes |
| 0023 | F | **segv** | `frame.rs:10092` | concurrent `next()` on a shared generator (rare, ~0.6%) | `ValueError` |
| 0024 | H | **segv** | `_ctypes/function.rs:182` | `import ctypes; ctypes.CDLL('libc.so.6').strlen(1.5)` | `ArgumentError` |

### 7.2 Static-scan surface (from `unwrap_scan`, RustPython @ `3290f287f`)
- **108** Rust-implemented modules (`rust_modules.txt`) — the crash-rich targeting surface.
- **973** risky panic-family lines (`risky_sites.tsv`), by reachability:
  **159 `py`** (direct attr), **126 `protocol`** (slot), **686 `internal`** (transitive), 1 other.
- By pattern: 686 `.unwrap()`, ~114 `.expect(`, plus `unreachable!`/`unimplemented!`/`todo!`/`panic!`/
  `.args[`. The `py`+`protocol` tiers (285 sites) are the primary triage queue for §5.1.
- **Limitation to fix in the toolkit:** `unwrap_scan` v1 does not re-map a `protocol` trait method to
  the owning class's Python protocol name across files, nor follow calls from an exposed fn into
  arbitrary helpers. The toolkit's mapper (§5.0) should close both.

### 7.3 Prior art (vs the RustPython tracker, as of the campaign)
Known/open upstream: **0003 = #5210**, **0007a = #2796** (umbrella), **Class J = #3493/#1779**. **0001**
is a distinct, unfixed variant of the #7813/#7965 thread-teardown family. **0004** is in the csv rework
area (#8310). The rest appear unreported. (Full table: `notes/prior-art-rustpython-tracker.md`.)

### 7.4 Reference paths
- Findings: `reports/RUSTPY-00NN-*/` (`report.md` + `repro.py` + `meta.json`).
- Catalog: `catalog/known_panics.tsv` (regen `scripts/gen_known_panics.py`).
- Per-fleet narrative: `INDEX.md`. Class notes: `notes/`.
- Static tool: `tools/unwrap_scan/` (`README.md`, `rust_modules.txt`, `risky_sites.tsv`).
- Fuzzer + dedup engine (sibling repo `fusil`): `fusil/python/rustpython_dedup.py`, the `--rustpython`
  plugin (`fusil_rustpython_plugin`), modes in `fusil/python/__init__.py` /
  `write_python_code.py::_write_tsan_stress_region`.

---

*Compiled from the fusil–RustPython campaign (fleets 01–09, findings RUSTPY-0001..0024). The most
actionable single takeaway for `rustpy-review-toolkit`: build §5.1 (`panic-site-auditor`) +
§5.9 (`cpython-parity-differential`) first — together they cover more than half the catalogue — then
add §5.2 (`unsafe-soundness`) and §5.3 (`thread-safety`) for the memory-unsafety long tail.*
