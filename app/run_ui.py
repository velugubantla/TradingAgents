"""CLI entrypoint to launch the local Chainlit web UI."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


def main() -> int:
    app_path = Path(__file__).with_name("chainlit_app.py").resolve()
    host = os.getenv("CHAINLIT_HOST", "127.0.0.1")
    port = os.getenv("CHAINLIT_PORT", "8000")
    watch = os.getenv("CHAINLIT_WATCH", "true").strip().lower() in {"1", "true", "yes", "y"}

    command = [
        sys.executable,
        "-m",
        "chainlit",
        "run",
        str(app_path),
        "-h",
        host,
        "-p",
        port,
    ]
    if watch:
        command.append("-w")

    return subprocess.call(command)


if __name__ == "__main__":
    raise SystemExit(main())

