# RUSTPY-0006 — `compile()`/`eval()` panic on surrogate strings (`builtins.rs` `expect_str`)

**2 vehicles — the "rare" panic** (its message prints mid-run, not at the tail, because it fires inside a
call the script keeps running past).

## Reproducer
```python
eval(chr(0xd800))                       # panics  (builtins.rs:557)
# also: compile(<str with a lone surrogate>, '<s>', 'exec')  (builtins.rs:607)
```
```
thread 'main' panicked at crates/vm/src/stdlib/builtins.rs:557:...:
PyStr contains surrogates
```

## Root cause
The `compile`/`eval` source-handling path does `source.expect_str().to_owned()`. `expect_str()` **panics**
("PyStr contains surrogates") when the `PyStr` holds lone surrogate code points (it assumes a
surrogate-free UTF-8 view). A Python `str` built from `chr(0xD800..0xDFFF)` is legal at the Python level,
so feeding one to `eval`/`compile` aborts the interpreter. CPython raises `ValueError`
("source code string cannot contain null bytes" has a sibling check; surrogates → a decode/`ValueError`).

## Fix sketch
Handle surrogate-containing `str` in the source path without `expect_str()` — surface a `ValueError`
(or `SyntaxError`) instead of panicking. More broadly, audit `expect_str()` call sites reachable from
Python input.

## Impact
`eval`/`compile` of an attacker-influenced string containing a surrogate aborts the interpreter.
