"""澄清集评测：统计从模糊需求收敛到「可推荐」的澄清轮次（M5 基线）。

仅驱动 clarify_node（只依赖 LLM，不碰检索/比价），按 clarify_set.json 的脚本逐轮喂答，
直到 needs_action=="shopping" 记为收敛；脚本轮次耗尽仍不收敛则记 max 并标记。

设计决策：v0 的 clarify 只读最新消息、不跨轮累积偏好，故统计「收敛率 + 已收敛轮次中位数」
两个数字——前者暴露不能累积的弱点，后者对照简历目标「澄清轮次中位数 ≤3」。
"""
import asyncio
import json
from pathlib import Path
from statistics import median

from langchain_core.messages import HumanMessage

from app.agents.nodes.clarify import clarify_node
from app.core.loop import install_selector_event_loop_policy

SET = Path(__file__).with_name("clarify_set.json")


def _initial(message: str) -> dict:
    return {
        "messages": [HumanMessage(message)],
        "preferences": {}, "intent": "", "needs_action": "",
        "candidates": [], "recommendation": {},
        "draft_order": None, "order": None,
        "lock_attempts": 0, "price_raised": False, "hop_count": 0,
    }


def _apply(state: dict, update: dict) -> None:
    """把节点返回的 update 手动并入 state（模拟图的消息 add reducer 与字段覆盖）。"""
    if "messages" in update:
        state["messages"] = state["messages"] + update["messages"]
    for k in ("preferences", "intent", "needs_action", "hop_count"):
        if k in update:
            state[k] = update[k]


async def _rounds_to_converge(turns: list[str]) -> tuple[int, bool]:
    """返回 (轮次, 是否收敛)。未收敛时轮次 = len(turns)。"""
    state = _initial(turns[0])
    for i in range(len(turns)):
        _apply(state, await clarify_node(state))
        if state["needs_action"] == "shopping":
            return i + 1, True
        if i + 1 < len(turns):
            state["messages"].append(HumanMessage(turns[i + 1]))
    return len(turns), False


async def main() -> int:
    cases = json.loads(SET.read_text(encoding="utf-8"))["cases"]
    rounds, converged = [], []
    for c in cases:
        r, ok = await _rounds_to_converge(c["turns"])
        rounds.append(r)
        converged.append(ok)
        flag = "" if ok else "  [未收敛]"
        print(f"[{c['id']}] {c['name']}: {r} 轮{flag}")

    ok_rounds = [r for r, ok in zip(rounds, converged) if ok]
    med = median(ok_rounds) if ok_rounds else 0
    rate = sum(converged) / len(converged) * 100
    print(f"\n收敛率: {sum(converged)}/{len(converged)} ({rate:.0f}%)")
    print(f"已收敛轮次中位数: {med} · 最大: {max(rounds)} · 各轮次: {rounds}")
    return 0


if __name__ == "__main__":
    install_selector_event_loop_policy()
    raise SystemExit(asyncio.run(main()))
