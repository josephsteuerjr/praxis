"""Cross-tree source fixtures also work from an external composed edition.

HELENE_TEST_SOURCE_ROOT names the clean source checkout used to compose the
consumer. It supplies test/build source only, never owner memory or credentials.
"""
import os
from pathlib import Path


def source_root(origin):
    explicit = os.getenv("HELENE_TEST_SOURCE_ROOT")
    candidates = [Path(explicit)] if explicit else Path(origin).resolve().parents
    for root in candidates:
        if (root / "desk/installer/candidate_tree.py").is_file() and (root / "helene/core/forge.py").is_file():
            return root
    raise RuntimeError("cross-tree tests require HELENE_TEST_SOURCE_ROOT pointing to the clean source checkout")
