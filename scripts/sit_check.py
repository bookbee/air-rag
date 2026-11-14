"""Run sit/queries.json against a running air-rag seeded with sit/corpus.

    make sit-check                       # against http://localhost:8087
    python scripts/sit_check.py --base-url http://host:8087 --key airr_...

Exits non-zero if any case fails, or if the index is empty (seeding failed).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import httpx

_ROOT = Path(__file__).resolve().parent.parent
#: Other-tenant and untenanted documents in sit/corpus: never a valid citation.
_NEVER_RETURNED = frozenset({"acme-returns", "draft-no-tenant"})


def _source(citation: dict[str, Any]) -> str:
    return str(citation.get("metadata", {}).get("source_id") or citation["source_id"].split("#")[0])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default="http://localhost:8087")
    parser.add_argument("--key", default="airr_local_dev_key")
    parser.add_argument("--tenant", default="brightcart", help="the key's tenant")
    parser.add_argument("--queries", type=Path, default=_ROOT / "sit" / "queries.json")
    args = parser.parse_args()

    headers = {"X-API-Key": args.key}
    cases = json.loads(args.queries.read_text(encoding="utf-8"))["cases"]
    with httpx.Client(base_url=args.base_url, headers=headers, timeout=30.0) as http:
        caps = http.get("/v1/capabilities").raise_for_status().json()
        if not caps.get("document_count"):
            sys.stdout.write("FAIL: index is empty — check air-rag's seed.* log lines\n")
            return 1
        sys.stdout.write(f"index: {caps['document_count']} chunks\n\n")

        failures = 0
        gaps = 0
        for i, case in enumerate(cases, start=1):
            within = int(case.get("within", 3))
            response = http.post(
                "/v1/retrieve",
                json={"query": case["query"], "top_k": max(within, 5)},
                headers={"X-Request-ID": f"sit-{i:02d}"},
            )
            if response.status_code != httpx.codes.OK:
                failures += 1
                sys.stdout.write(f"FAIL [{i:02d}] HTTP {response.status_code}: {case['query']}\n")
                continue
            citations = response.json()["citations"]
            leaks = [
                c["source_id"]
                for c in citations
                if c.get("metadata", {}).get("tenant") != args.tenant
                or c.get("metadata", {}).get("status") != "active"
                or _source(c) in _NEVER_RETURNED
            ]
            if leaks:
                failures += 1
                sys.stdout.write(f"FAIL [{i:02d}] isolation leak {leaks}: {case['query']}\n")
                continue
            ranked = [_source(c) for c in citations]
            rank = ranked.index(case["expect"]) + 1 if case["expect"] in ranked[:within] else None
            top_ok = not ranked or ranked[0] != case.get("forbid_top")
            ok = rank is not None and top_ok
            known_gap = bool(case.get("known_gap"))
            label = "PASS" if ok else ("GAP " if known_gap else "FAIL")
            failures += not ok and not known_gap
            gaps += not ok and known_gap
            top = f"{ranked[0]} ({citations[0]['score']:.3f})" if citations else "-"
            sys.stdout.write(
                f"{label} [{i:02d}] rank={rank or '-':<2} top={top:<40} {case['query']}\n"
            )

    passed = len(cases) - failures - gaps
    sys.stdout.write(f"\n{passed}/{len(cases)} passed, {gaps} known gap(s), {failures} failed\n")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
