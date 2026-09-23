"""跨会话偏好记忆离线评测：只复用确定性解析与更新规则。"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

from app.agents.nodes.memory import parse_memory_command
from app.services.memory import DURABLE_MEMORY_KEYS
from app.services.profile import _normalize_remove_values, normalize_updates


MEMORY_SET = Path(__file__).with_name("memory_set.json")


def apply_offline_update(profile: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    """在内存中复现 profile.apply_updates 的数据语义，不访问 PostgreSQL。"""
    values, forgotten, _provenance = normalize_updates(updates)
    result = {
        key: deepcopy(value)
        for key, value in profile.items()
        if key in DURABLE_MEMORY_KEYS
    }

    for key in forgotten:
        result.pop(key, None)

    for key, remove_values in _normalize_remove_values(updates).items():
        if key in forgotten or key in values:
            continue
        existing = result.get(key)
        if not isinstance(existing, list):
            continue
        remaining = [value for value in existing if value not in remove_values]
        if remaining:
            result[key] = remaining
        else:
            result.pop(key, None)

    for key, value in values.items():
        result[key] = deepcopy(value)
    return result


def evaluate_cases(cases: list[dict[str, Any]], *, verbose: bool = False) -> dict[str, Any]:
    """运行独立案例，返回可供 pytest 和 CLI 复用的汇总。"""
    failures: list[str] = []
    passed = 0

    for case in cases:
        case_id = str(case["id"])
        profiles = deepcopy(case["initial_profiles"])
        profile_key = str(case["profile_key"])
        command = parse_memory_command(str(case["message"]))
        profiles[profile_key] = apply_offline_update(profiles.get(profile_key, {}), command)

        reasons: list[str] = []
        if command != case["expected_command"]:
            reasons.append(f"命令={command!r}，期望={case['expected_command']!r}")
        if profiles != case["expected_profiles"]:
            reasons.append(f"画像={profiles!r}，期望={case['expected_profiles']!r}")

        if reasons:
            failures.append(f"{case_id}: {'；'.join(reasons)}")
            if verbose:
                print(f"[FAIL] {case_id}: {'；'.join(reasons)}")
            continue
        passed += 1
        if verbose:
            print(f"[PASS] {case_id}")

    return {"total": len(cases), "passed": passed, "failures": failures}


def main() -> int:
    cases = json.loads(MEMORY_SET.read_text(encoding="utf-8"))["cases"]
    result = evaluate_cases(cases, verbose=True)
    total = result["total"]
    passed = result["passed"]
    ratio = passed / total * 100 if total else 0.0
    print(f"\n记忆离线评测通过率: {passed}/{total} ({ratio:.1f}%)")
    for failure in result["failures"]:
        print(f"  - {failure}")
    return 1 if result["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
