"""Export the generated OpenAPI schema to a file.

Usage::

    python -m scripts.export_openapi [OUTPUT_PATH]

The default output is ``docs/openapi.json``. Keeping the schema in version
control makes API changes reviewable and lets client SDKs be generated without
running the service.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from app.main import app


def main() -> int:
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("docs/openapi.json")
    schema = app.openapi()

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    operations = sum(len(operations) for operations in schema["paths"].values())
    print(f"Wrote {output} ({len(schema['paths'])} paths, {operations} operations)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
