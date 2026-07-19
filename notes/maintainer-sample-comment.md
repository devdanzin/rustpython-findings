The first run against RustPython 0.5.0, for about 50 minutes on 4 instances, turned up a batch of pure-Python programs that panic or segfault the interpreter where CPython raises a normal exception.

Here's a sample. Each row is a complete reproducer — paste it into `rustpython -c "…"`:

| reproducer | crash | CPython does |
|---|---|---|
| `import pwd; pwd.struct_passwd().pw_name` | panic `types/structseq.rs:311` — `index out of bounds` (a struct-sequence built with fewer elements than its named fields; the field getter indexes with no bounds check) | `TypeError` at construction |
| `import _md5; _md5.md5()` | panic `class.rs:87` — `static type has not been initialized` | returns an md5 object |
| `import _csv; _csv.reader([]).__next__()` | panic `stdlib/src/csv.rs:805` — `unwrap()` on `None` (unregistered dialect line terminator) | works |
| `import _typing; _typing._idfunc()` | panic `stdlib/_typing.rs:43` — `index out of bounds` (`args[0]`, no arity check) | `TypeError` |
| `eval(chr(0xd800))` | panic `stdlib/builtins.rs:557` — `PyStr contains surrogates` (`expect_str` on a lone-surrogate string) | `ValueError` |
| `import re; M=type(re.match('a','a')); M.__new__(M)[0]` | SIGSEGV in `_sre` `Match::as_mapping` — an uninitialized `Match` (via `__new__`) has garbage `regs`/`string`; the mapping-subscript path reads them with no init guard (`group()`/`__repr__` do guard it) | `TypeError` |

That last one is memory-unsafety (a segfault, not a clean panic). Two more that take a few lines rather than one:

- **The most common panic (48 hits): `stdlib/_thread.rs:977` `RefCell already borrowed`.** `cleanup_thread_local_data` holds `LOCAL_GUARDS.borrow_mut()` across `.clear()`, which drops the guards; a dropped thread-local value whose `__del__` re-enters `_thread._local` borrows `LOCAL_GUARDS` again → `BorrowMutError`. (Full reproducer + root cause in the catalog.)
- **A segfault class: native stack overflow from unbounded recursion** — hashing/comparing a deep or cyclic object recurses on the native stack with no depth guard → SIGSEGV/SIGABRT, where CPython raises `RecursionError`.

Full catalog — one report per crash, each with a root cause and a fix sketch:
**https://github.com/devdanzin/rustpython-findings**
An example of what one report looks like (the `re.Match` segfault):
**https://gist.github.com/devdanzin/6e8f53711ea6b9c311e2c8fa043b17ca**

If this is useful I'm happy to file these individually or as an umbrella issue, minimize the remaining segfaults, or run more fuzzing, whatever's most helpful.

*Found with [fusil](https://github.com/devdanzin/fusil). Investigation, root causes, fixes, reproducers and draft create with help from Claude Code*
