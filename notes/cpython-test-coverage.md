# Would CPython's own unittests already catch these? (2026-07-19)

The maintainers ([#8325](https://github.com/RustPython/RustPython/issues/8325)) asked whether these
findings are "a duplication of CPython unittests" — i.e. if you ran CPython's `Lib/test/` suite under
RustPython, would it already surface them? Checked each against CPython `main`'s test suite + verified
RustPython's actual behavior. Answer: **2 of 9 are directly covered by a CPython test; the other 7 exercise
inputs/behaviors the suite doesn't** — including one where RustPython *passes* the relevant CPython tests.

| finding | CPython does | CPython test that exercises it | covered? |
|---------|--------------|-------------------------------|----------|
| **RUSTPY-0008** `re.Match.__new__` segfault | `re.Match()` → `TypeError: cannot create 're.Match' instances` | **`test_re.py:3160` `check_disallow_instantiation(re.Match)`** asserts it can't be instantiated. RustPython *lets* `re.Match()` construct → the assertion fails, and `[...]` on the instance segfaults. | **YES** — running `test_re` catches it. |
| **RUSTPY-0004** csv reader unwrap | reader iterates fine | `test_csv.py` iterates readers pervasively. In RustPython **every** reader iteration panics (`csv.reader(['a,b']) ` panics via `get_lineterminator`). | **YES** — `test_csv` would crash immediately. |
| **RUSTPY-0002** structseq short-ctor field OOB | `pwd.struct_passwd()` → `TypeError: missing required argument 'sequence'` | No test constructs a too-short / no-arg struct-seq and then reads a field. `test_structseq` tests the evil-`__getitem__` case (correct length) and copy/replace, not this. | **no** |
| **RUSTPY-0005** `_typing._idfunc()` arity OOB | `TypeError` (wrong arity) | `test_typing` never calls the internal `_typing._idfunc` with zero args. | **no** |
| **RUSTPY-0006** `eval`/`compile` surrogate source | `UnicodeEncodeError` | **No** `test_compile`/`test_builtin` test compiles/evals a lone-surrogate *source* string (surrogate tests there are for `ascii()`/`repr()`, not `compile`). | **no** |
| **RUSTPY-0001** `_thread._local` teardown re-entrancy | works | `test_threading_local` has `test_derived_cycle_dealloc` (a local subclass with a ref cycle) — adjacent, but **not** the "a stored value's `__del__` re-enters `_local` while the owning thread runs cleanup" trigger. | **no** (adjacent only) |
| **RUSTPY-0003** "static type not initialized" | n/a — CPython has no such failure mode | It's a RustPython-internal type-init issue (tracked as #5210), not a CPython behavior; nothing in CPython's suite targets it. | **no** (RustPython-specific) |
| **RUSTPY-0007a** recursion → native stack overflow | `RecursionError` | `test_json/test_recursion` (incl. `test_highly_nested_objects_encoding/decoding`, "doesn't segfault"), `test_richcmp`, `test_isinstance` test recursion. **But RustPython PASSES these** — it *guards* cyclic `json.dumps` (raises), deep-nested json (raises), recursive `__eq__`/`hash`/compare (`RecursionError`). The fleet's segfaults come from other, more specific object graphs. | **no** — RustPython passes the standard recursion tests; the fuzzer found triggers they don't cover. (Known *class* = #2796.) |
| **RUSTPY-0009** `repr(staticmethod(obj))` w/ raising `__repr__` | propagates the inner exception (`ValueError`) | `test_reprlib.py:262` reprs a staticmethod — but wrapping a **normal** function (`staticmethod(C.foo)`), which RustPython reprs fine (no panic). No test does `repr(staticmethod(obj_with_raising___repr__))`, the input that triggers the `.unwrap()` panic. | **no** — the general path is tested, but not the raising-`__repr__` case that crashes. |

## Takeaways for the maintainers

- **Only 2 of 9 (RUSTPY-0008, RUSTPY-0004) would be caught by running CPython's test suite as-is.** The
  other 7 are inputs the suite doesn't exercise — wrong-arity/uninitialized-object construction, internal
  functions called directly, surrogate *source*, thread-teardown re-entrancy, a RustPython-internal
  type-init bug, and recursion object-graphs that slip past the guards RustPython already has.
- **RUSTPY-0007a is the interesting one:** RustPython *passes* CPython's recursion tests (its guards work
  for the tested cases), yet the fuzzer still produced recursion-related segfaults — so it's not a
  duplicate of those tests, it's the tail they don't reach (the known #2796 class).
- **Even for the 2 that are "covered":** a fuzzer's minimal one-line reproducer pinpoints the exact crash,
  whereas a test-suite hit aborts the whole test file (and, when it segfaults, takes the runner with it),
  which is harder to bisect. So fuzzing complements the CPython suite rather than duplicating it.

*(Internal tracking only — not reflected in the maintainer comment or the gist.)*
