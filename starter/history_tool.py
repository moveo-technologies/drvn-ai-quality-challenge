"""
get_test_history — a local tool the triage agent can offer to the model.

Reads fixtures/history/*.json. No network.

    from starter.history_tool import get_test_history, GET_TEST_HISTORY_SPEC

    get_test_history("payment/checkout.spec.ts::should capture card payment",
                     before_date="2026-09-18", nights=5)
    -> [{"date": "2026-09-17", "failed": True, "error": "Error: expect(...)", "retry_count": 0}, ...]

Nights where the test did not fail are returned with failed=False.
"""

from __future__ import annotations

import json
from pathlib import Path

DEFAULT_HISTORY_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "history"

GET_TEST_HISTORY_SPEC = {
    "name": "get_test_history",
    "description": "Return the outcome of a Playwright test on the previous nightly runs.",
    "input_schema": {
        "type": "object",
        "properties": {
            "test_id": {"type": "string", "description": "'<test_file>::<test_title>'"},
            "before_date": {"type": "string", "description": "YYYY-MM-DD, exclusive"},
            "nights": {"type": "integer", "minimum": 1, "maximum": 14, "default": 5},
        },
        "required": ["test_id"],
    },
}


def get_test_history(test_id: str, before_date: str | None = None, nights: int = 5,
                     history_dir: str | Path = DEFAULT_HISTORY_DIR) -> list[dict]:
    if "::" not in test_id:
        raise ValueError("test_id must be '<test_file>::<test_title>'")
    test_file, test_title = test_id.split("::", 1)
    nights = max(1, min(int(nights), 14))
    files = sorted(Path(history_dir).glob("*.json"))
    if before_date:
        files = [f for f in files if f.stem < before_date]
    out = []
    for f in files[-nights:]:
        run = json.loads(f.read_text())
        hit = next((r for r in run.get("raw_failures", [])
                    if r.get("test_file") == test_file and r.get("test_title") == test_title), None)
        out.append({
            "date": run.get("date", f.stem),
            "failed": hit is not None,
            "error": hit["error_message"].split("\n")[0] if hit else None,
            "retry_count": hit.get("retry_count") if hit else None,
        })
    return out
