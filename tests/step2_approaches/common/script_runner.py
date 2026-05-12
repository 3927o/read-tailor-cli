#!/usr/bin/env python3
"""Run an AI-generated normalize.py script (used by plan_1/2/3)."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from typing import Any


def run_generated_script(
    script_text: str,
    raw_html_path: str,
    output_html_path: str,
    timeout: int = 180,
) -> dict[str, Any]:
    if not script_text.strip():
        raise RuntimeError("AI returned empty script content")

    work_dir = tempfile.mkdtemp(prefix="step2_script_")
    script_path = os.path.join(work_dir, "normalize.py")
    try:
        with open(script_path, "w", encoding="utf-8") as fh:
            fh.write(script_text)
        proc = subprocess.run(
            [sys.executable, script_path, raw_html_path, output_html_path],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "returncode": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "script_path_kept": script_path if proc.returncode != 0 else None,
        }
    finally:
        if os.path.exists(work_dir):
            # On failure we want to inspect the script: keep the dir.
            # On success we clean up.
            keep = False
            try:
                proc_returncode = proc.returncode  # type: ignore[name-defined]
                keep = proc_returncode != 0
            except NameError:
                keep = True
            if not keep:
                shutil.rmtree(work_dir, ignore_errors=True)
