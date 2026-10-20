"""Inspect compact pressure after a place-key migration; never write for the acting author.

Use --dry-run for a pressure inventory. The old automatic drain is retired:
memory_compact prepare / fold(selection, text) belongs to Praxis's own turn.
"""
from __future__ import annotations

import argparse
import json

import memory_life as ml


def places() -> list[str]:
    return sorted(p.stem for p in ml.STATE_DIR.glob("*.json"))


def drain(place: str, *, limit: int) -> dict:
    return {"place": place, "folded": 0, "compacts": 0,
            "stopped": "acting_author_required", "sec": 0,
            "instruction": "Use memory_compact prepare, then fold(selection, text)."}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=200, help="legacy argument; no automatic drain")
    parser.add_argument("--place", default="", help="one place only")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if not args.dry_run:
        print(json.dumps(drain(args.place, limit=args.limit)))
        return 2
    for place in ([args.place] if args.place else places()):
        state = ml._load_state(place, rebuild=False)
        rows = state.get("hot") or []
        print(json.dumps({"place": place, "hot": len(rows),
                          "plan": ml.plan_hot_fold(rows, place=place)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
