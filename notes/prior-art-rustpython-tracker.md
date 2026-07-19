# Prior-art check vs the RustPython tracker (2026-07-19)

Searched RustPython/RustPython issues + PRs (`gh api search/issues`) for each finding. Build under test =
RustPython 0.5.0 @ `a9c2c529b` (Jul 13 2026), so any fix merged before that is already in the binary we
crashed.

| finding | crash | status vs tracker |
|---------|-------|-------------------|
| **RUSTPY-0001** | `_thread.rs:977` RefCell double-borrow | **Distinct + unfixed.** Same *family* as **#7813 / PR #7965** ("Fix thread teardown panic when weakref callback fires during cleanup") — but that panic is a **different** one (`push_thread_frame called without initialized thread slot`, `vm/thread.rs:451`, the `CURRENT_THREAD_SLOT` path). #7965 is **already merged into our build** (its "weakref callback fired during that drop" comments are in `_thread.rs:568/1750`), yet our `RefCell already borrowed` at `_thread.rs:977` (the `LOCAL_GUARDS` / `cleanup_thread_local_data` re-entrancy) still reproduces → our variant is unreported. Worth filing, cross-referencing #7813/#7965. |
| **RUSTPY-0002** | `structseq.rs:311` field-getter OOB | **Appears new.** structseq robustness has been touched (#7627 struct_time overflow→OverflowError, #6327 PyStructSequence compat) but no issue for the empty/short struct-seq field-getter OOB. |
| **RUSTPY-0003** | `class.rs:87` "static type not initialized" | **KNOWN, open → #5210** ("static type has not been initialized when type lives in another module"). Our `_md5.md5()` is a concrete instance. |
| **RUSTPY-0004** | `csv.rs:805` `unwrap()` on None | **Active-rework area.** csv is under heavy churn (#8310 reader-architecture RFC, #8324 "reject reentrant reader advancement", #8304/#8254 dialect/lineterminator work). No issue names the `get_lineterminator` unwrap(None) panic specifically; likely in-scope of the rework — worth a heads-up on #8310. |
| **RUSTPY-0005** | `_typing.rs:43` `_idfunc()` arity OOB | **Appears new** (no hits for `_idfunc` / typing index-OOB). |
| **RUSTPY-0006** | `builtins.rs` `expect_str` surrogate panic | **Appears new / unfiled.** compile()/eval() CPython-parity is in progress (#7767 open, #8138 merged) but nothing names the surrogate `expect_str` panic. |
| **RUSTPY-0007a** | recursion → native stack overflow (hash/compare) | **KNOWN class, open umbrella → #2796** ("Recursions in rust code trigger segmentation faults"; also #1374). Per-area fixes have landed (json #7632, AST/compile #7630, musl VM #7558, parser #7655 open) but the general hash/compare recursion → SIGSEGV remains. |
| **RUSTPY-0008** | `_sre` `Match::as_mapping` segfault (uninitialized `__new__`) | **Appears new.** SRE has had unsafe-`unwrap` hardening (#7435) but nothing for subscripting an uninitialized `Match`. |

## Summary

- **Known / already tracked (2):** RUSTPY-0003 = **#5210** (open); RUSTPY-0007a = **#2796** (open umbrella).
- **Related family, but our variant is distinct + unfixed (1):** RUSTPY-0001 vs #7813/#7965.
- **Active-rework area, not specifically filed (1):** RUSTPY-0004 (csv, cf. #8310).
- **Appear new / unfiled (4):** RUSTPY-0002 (structseq), RUSTPY-0005 (`_typing._idfunc`), RUSTPY-0006 (surrogates), RUSTPY-0008 (`_sre` Match segfault).

So ~4–5 of the 8 look genuinely unreported; the two "known" ones (#5210, #2796) are still open, so our
concrete one-line reproducers are still useful corroboration.
