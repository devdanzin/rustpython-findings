# RUSTPY-0008 — segfault: subscripting an uninitialized `re.Match` (`_sre` `Match::as_mapping`)

**Deterministic SIGSEGV (6/6), minimized to 3 lines.** Creating a `re.Match` via `__new__` (which skips
the normal match construction) leaves its internal `regs`/`string`/`pattern` uninitialized; the mapping
**subscript** path reads them without an init check → reads garbage / null → segfault. This is the
minimized form of the `re`-module segfault seen in the fleet (vehicle's last op was `Match.__getitem__()`).

## Reproducer

```python
import re
M = type(re.match('a', 'a'))   # the Match type
M.__new__(M)[0]                # SIGSEGV
```

gdb:
```
#0 <rustpython_vm::stdlib::_sre::_sre::Match as ...AsMapping>::as_mapping::{closure}::{closure}
#1 <PyObject>::as_mapping / PyMapping subscript
...
```

## Root cause

`crates/vm/src/stdlib/_sre.rs`. The mapping subscript downcasts and calls `__getitem__` with **no
initialization guard**:

```rust
impl AsMapping for Match {
    fn as_mapping() -> &'static PyMappingMethods {
        ... subscript: atomic_func!(|mapping, needle, vm| {
            Match::mapping_downcast(mapping)
                .__getitem__(needle.to_owned(), vm)      // no check that the Match was initialized
                .map(|x| x.to_pyobject(vm))
        }), ...
    }
}
```

`__getitem__` → `get_slice(i, str_drive, vm)` reads `self.regs[index]` and slices `self.string`:

```rust
fn get_slice<S: SreStr>(&self, index: usize, str_drive: S, vm) -> Option<PyObjectRef> {
    let (start, end) = self.regs[index];               // regs uninitialized -> garbage / OOB
    ...
    Some(str_drive.slice(start as usize, end as usize, vm))   // string uninitialized -> bad ptr
}
```

A `Match` built by `M.__new__(M)` never ran the SRE match, so `regs`/`string`/`pattern` are default/garbage.
The `group()` and `__repr__` paths **do** guard this (they raise `TypeError: Expected type 'Match'…` /
"unexpected payload"), but the `AsMapping` subscript path does not — so `m[0]` dereferences uninitialized
state and segfaults.

## Fix sketch

Make the Match's match-state non-optional-at-the-type-level or guard it: `__getitem__`/`get_slice` (and any
other `AsMapping`/method path) should verify the Match is initialized (as `group()`/`__repr__` already do)
and raise instead of indexing `self.regs`/slicing `self.string`. Ideally `Match::__new__` should refuse to
create an unbound instance, or store the match state as `Option<…>` checked on every access.

## Impact

A 3-line pure-Python program segfaults the interpreter — memory-unsafety reachable via `re.Match.__new__`,
which any code can call (`type(re.match(...)).__new__(...)`).
