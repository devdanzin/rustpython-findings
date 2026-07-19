# RUSTPY-0012 — `_suggestions._generate_suggestions` eagerly collects any iterable (CPython requires a list) → unbounded memory / OOM abort

**New in fusil-rustpython_07 — a memory *balloon*, not a single huge allocation.** RustPython's
`_generate_suggestions` accepts **any iterable** for its `candidates` argument and eagerly collects
it into a `Vec`; CPython requires an actual `list` and raises `TypeError` otherwise. So an infinite
(or merely enormous) iterable makes RustPython grow memory unboundedly — ~180–1000 MiB/s in a tight
loop (the `cpu_load` marker) — until the allocator aborts (`memory allocation of N bytes failed`).
No threads or concurrency needed: a single direct call does it.

## Reproducer

```python
import _suggestions, itertools
_suggestions._generate_suggestions(itertools.count(), "x")   # infinite iterator -> unbounded Vec
```

Also via the legacy sequence protocol (an object with a `__getitem__` that never raises
`IndexError` and no `__iter__` — the fuzzer hit this with a `keys()`/`__getitem__` object):

```python
class InfGetitem:
    def __getitem__(self, i): return object()
_suggestions._generate_suggestions(InfGetitem(), "ab")       # obj[0], obj[1], ... forever
```

Both grow RSS steadily (measured: 5.5 GiB in 5 s for `itertools.count()`) until OOM/abort.

CPython (3.14): **both raise immediately** —

```
TypeError: candidates must be a list
```

## Root cause

`crates/stdlib/src/suggestions.rs`:

```rust
#[pyfunction]
fn _generate_suggestions(
    candidates: Vec<PyObjectRef>,     // <-- FromArgs eagerly ITERATES + collects any iterable
    name: PyObjectRef,
    vm: &VirtualMachine,
) -> PyObjectRef {
    match crate::vm::suggestion::calculate_suggestions(candidates.iter(), &name) { ... }
}
```

The `candidates: Vec<PyObjectRef>` parameter is extracted by iterating the Python argument and
collecting **every** element into a `Vec` up front — with no length bound and no type check. CPython's
C implementation instead does `PyList_Check(candidates)` first and raises `TypeError("candidates must
be a list")`, so it never iterates an untrusted object. A `list` is already fully materialised, so
requiring one both restores parity **and** removes the unbounded-collection path (you can't construct
an infinite `list`).

This is two bugs in one:
1. **Type-parity gap** — RustPython accepts `tuple`/`str`/`dict`/iterator/`__getitem__`-object where
   CPython accepts only `list`.
2. **Unbounded eager collection** — an infinite/huge iterable balloons memory to OOM (a
   denial-of-service on any code that forwards user input to this internal helper).

## Fix sketch

Take a `PyListRef` (or type-check `PyList`) instead of `Vec<PyObjectRef>`:

```rust
fn _generate_suggestions(candidates: PyListRef, name: PyObjectRef, vm: &VirtualMachine) -> ... {
    // iterate candidates.borrow_vec() -- already materialised, bounded, list-only (CPython parity)
}
```

emitting `TypeError: candidates must be a list` for non-lists.

## Impact / broader class

A one-line pure-Python call aborts the interpreter after ballooning to multi-GiB. More broadly, the
`: Vec<PyObjectRef>` argument pattern — eager unbounded collection of an untrusted, possibly infinite
iterable — appears at ~60 sites across `crates/*/src` (a subset are `#[pyfunction]`/`#[pymethod]`
arguments, e.g. `lzma.rs` `filter_specs`). Each is a candidate for the same balloon where CPython
would type-check or stream. Worth an audit pass for argument positions that CPython restricts to a
concrete container.

## Prior art

_To check vs the RustPython tracker (`_generate_suggestions` / `candidates` / unbounded iterable)._
Appears unreported. Distinct from the recursion→stack-overflow (RUSTPY-0007a) and the single-huge-
allocation abort class: this is unbounded *incremental* collection of an untrusted iterable.
