# RUSTPY-0005 — `_typing._idfunc()` indexes `args[0]` with no arity check (`_typing.rs:43`)

**2 vehicles.** Calling `_typing._idfunc` with no arguments panics.

## Reproducer
```python
import _typing
_typing._idfunc()      # panics
```
```
thread 'main' panicked at crates/vm/src/stdlib/_typing.rs:43:18:
index out of bounds: the len is 0 but the index is 0
```

## Root cause
```rust
#[pyfunction]
pub(crate) fn _idfunc(args: FuncArgs, _vm: &VirtualMachine) -> PyObjectRef {
    args.args[0].clone()      // :43  <-- no check that at least one positional arg was passed
}
```
A no-arg call makes `args.args` empty; `args.args[0]` is an out-of-bounds index → panic. CPython's
`_idfunc` raises `TypeError` on wrong arity.

## Fix sketch
Validate arity (declare the parameter, or check `args.args.len()` and raise `TypeError`) before indexing.

## Impact
One-line pure-Python abort. Same unchecked-indexing anti-pattern as RUSTPY-0002.
