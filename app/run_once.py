"""CLI entrypoint for a single headless opportunities run."""

from __future__ import annotations

import json

from app.runner import RunnerService, load_settings_from_env


def main() -> int:
    settings = load_settings_from_env()
    runner = RunnerService(settings=settings)
    result = runner.run_once(source="cli")
    print(json.dumps(result, indent=2, ensure_ascii=True))

    if result.get("status") == "failed":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

