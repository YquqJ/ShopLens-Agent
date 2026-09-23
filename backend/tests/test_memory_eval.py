import json
from pathlib import Path

from app.eval.memory_eval import evaluate_cases


def test_bundled_memory_evaluation_covers_the_five_v1_behaviors() -> None:
    fixture = Path(__file__).parents[1] / "app" / "eval" / "memory_set.json"
    cases = json.loads(fixture.read_text(encoding="utf-8"))["cases"]

    result = evaluate_cases(cases)

    assert len(cases) == 5
    assert result == {"total": 5, "passed": 5, "failures": []}
