# unwrap_scan

Static scan of RustPython source for **Python-reachable panic sites** — the dominant crash class
in this catalog (a native `.unwrap()` / `.expect()` / `panic!` / unchecked index reached from
Python). It walks the Rust-implemented stdlib + builtins with [`syn`], attributes each risky line to
the function/method it lives in, and emits two artifacts that drive targeted fuzzing.

## Build & run

```sh
cargo build --release
./target/release/unwrap_scan /path/to/RustPython [OUT_DIR]
# or:  RUSTPYTHON_ROOT=/path/to/RustPython ./target/release/unwrap_scan
```

Outputs (into `OUT_DIR`, default `.`):

- **`rust_modules.txt`** — the sorted native (Rust-implemented) module names. Feed straight to
  fusil so a fleet targets the crash-rich native surface instead of the ~200 mostly-pure-Python
  modules:
  ```sh
  fusil-python-threaded --python /path/to/rustpython --modules-file rust_modules.txt ...
  ```
- **`risky_sites.tsv`** — `module \t name \t kind \t reachable \t pattern \t file:line`, one row per
  risky line, sorted with directly-exposed sites first.

## The `reachable` column

- **`py`** — directly exposed via `#[pyfunction]` / `#[pymethod]` / `#[pygetset]` /
  `#[pyslot]` / `#[pystaticmethod]` / `#[pyclassmethod]`. The `name` is the Python name (the
  attribute's `name = "..."` override, else the Rust ident; `#[pymethod(magic)]` → `__ident__`).
  Highest-value targeting: call these directly with hostile/wrong-arity args.
- **`protocol`** — a slot method of a protocol-trait impl (`impl Representable for X`,
  `impl AsMapping for X`, …) — the `#[pyclass(with(Trait))]` surface. Python-reachable via the
  protocol (repr/subscript/iter/hash/…), just not through a `#[pymethod]` attr. The catalog's
  staticmethod-repr (RUSTPY-0009, `Representable`) and `re.Match` subscript (RUSTPY-0008,
  `AsMapping`) findings live here.
- **`internal`** — a helper fn reached transitively from an exposed one (e.g. csv's
  `get_lineterminator` / `FormatOptions.result`). The risk is real but one call away from the
  Python surface.

## Patterns

`.unwrap()`, `.expect(`, `panic!`, `unreachable!`, `unimplemented!`, `todo!`, and `.args[` (the
arity-OOB index that produced RUSTPY-0005 `_typing._idfunc`).

## Scope & limitations (v1)

- Scans `crates/vm/src/stdlib`, `crates/stdlib/src`, `crates/vm/src/builtins`, `crates/vm/src/types`.
- Attribution is per-function via `syn` (accurate module/function/method names). It does **not** yet
  re-map a `protocol` trait method back to the *owning class's* Python protocol name across files, nor
  follow calls from an exposed fn into arbitrary helpers — `internal` rows show where the risk sits,
  not which Python call reaches them. Line numbers track the scanned checkout (see the committed
  artifacts' provenance header), not any released build.

[`syn`]: https://docs.rs/syn
