#!/usr/bin/env python3
"""Fail fast when the image's document toolchain is incomplete."""

from __future__ import annotations

import importlib
import shutil
import subprocess
import sys


# Distribution names are not always import names (python-docx -> docx, etc.).
IMPORTS = (
    "pandas",
    "openpyxl",
    "xlsxwriter",
    "python_calamine",
    "docx",
    "docxtpl",
    "pptx",
    "pypdf",
    "pdfplumber",
    "pypdfium2",
    "reportlab",
    "PIL",
    "defusedxml",
    "msoffcrypto",
    "charset_normalizer",
)


def main() -> int:
    failures: list[str] = []
    for module_name in IMPORTS:
        try:
            importlib.import_module(module_name)
        except Exception as exc:  # report every missing/broken binary module at once
            failures.append(f"import {module_name}: {exc!r}")

    office = shutil.which("libreoffice") or shutil.which("soffice")
    if office is None:
        failures.append("libreoffice/soffice executable not found")
    else:
        result = subprocess.run(
            [office, "--headless", "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            failures.append(f"{office} --headless --version exited {result.returncode}: {detail}")

    if failures:
        print("office/PDF smoke check failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1

    print(f"office/PDF smoke check passed ({len(IMPORTS)} imports; {office})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
