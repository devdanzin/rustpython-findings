#!/usr/bin/env python3
"""Generate catalog/known_panics.tsv: a flat, read-only dedupe snapshot derived from the
canonical catalog (reports/*/meta.json). Fuzzer instances (fusil --rustpython-dedup-catalog)
load this instead of parsing every meta.json.

Row format (tab-separated):  <bug_id>\t<signature>
where <signature> is a fusil rustpython_dedup panic-site key, ``crates/<path>.rs:<line>`` (the
column is dropped; absolute checkout paths are normalised to the ``crates/`` tail). A finding may
carry several signatures (variant panic faces seen across runs -- e.g. the csv reader/writer
faces); each becomes a row. Segfault findings (kind == "segv") carry no panic signature -- they
surface as ``rustpySEGV`` / need gdb resolution -- so they contribute no rows.

This mirrors the sibling cpython-tsan-findings ``scripts/gen_known_races.py``; the signature
format is a cross-repo contract with fusil's ``rustpython_dedup.parse_report``.
"""

import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
OUT = ROOT / "catalog" / "known_panics.tsv"


def main():
    rows = set()
    ids = set()
    segv_only = []
    for meta in sorted(REPORTS.glob("*/meta.json")):
        d = json.loads(meta.read_text())
        if d.get("status") == "folded":
            continue  # retired id, merged into another finding that carries the signature
        rid = d["id"]
        ids.add(rid)
        sigs = [s.strip() for s in d.get("signatures", []) if s.strip()]
        for sig in sigs:
            rows.add((rid, sig))
        if not sigs:
            segv_only.append(rid)
    OUT.parent.mkdir(exist_ok=True)
    with OUT.open("w") as fh:
        fh.write("# bug_id\tsignature\n")
        for rid, sig in sorted(rows):
            fh.write("%s\t%s\n" % (rid, sig))
    print("wrote %s: %d signatures for %d findings" % (OUT.relative_to(ROOT), len(rows), len(ids)))
    if segv_only:
        print(
            "  (%d segfault finding(s) carry no panic signature -> rustpySEGV: %s)"
            % (len(segv_only), ", ".join(sorted(segv_only)))
        )


if __name__ == "__main__":
    main()
