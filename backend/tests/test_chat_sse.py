import json
from types import SimpleNamespace

import pytest

from app.api.chat import _stream


class MemoryEventGraph:
    async def astream(self, _payload, _cfg, stream_mode: str):
        assert stream_mode == ["updates", "custom"]
        yield "updates", {
            "load_memory": {
                "profile_preferences": {"preferred_colors": ["秘密黑"]},
                "memory_events": [{"type": "memory_read", "keys": ["preferred_colors"], "count": 1}],
            }
        }
        yield "updates", {
            "capture_memory": {
                "profile_preferences": {"preferred_materials": ["秘密羊毛"]},
                "memory_events": [{"type": "memory_write", "action": "write", "keys": ["preferred_materials"]}],
            }
        }

    async def aget_state(self, _cfg):
        return SimpleNamespace(interrupts=[], values={"order": None})


@pytest.mark.anyio
async def test_memory_node_sse_excludes_profile_preference_values() -> None:
    frames = [frame async for frame in _stream(MemoryEventGraph(), {}, {}, is_resume=False)]
    node_payloads = [
        json.loads(frame.split("data: ", 1)[1])
        for frame in frames
        if "event: node" in frame
    ]

    assert [payload["node"] for payload in node_payloads] == ["load_memory", "capture_memory"]
    assert all("profile_preferences" not in payload for payload in node_payloads)
    assert "秘密黑" not in "".join(frames)
    assert "秘密羊毛" not in "".join(frames)


class RecommendationPreviewGraph:
    async def astream(self, _payload, _cfg, stream_mode: str):
        assert stream_mode == ["updates", "custom"]
        yield "custom", {
            "event": "recommendation_preview",
            "recommendation": {"summary": "", "items": [{"sku_id": "SKU1"}], "degraded_sources": []},
            "recommendation_intro": "根据您的需求，为你推荐1款候选",
        }
        yield "updates", {"recommend": {"recommendation": {"summary": "完整理由", "items": [{"sku_id": "SKU1"}]}}}

    async def aget_state(self, _cfg):
        return SimpleNamespace(interrupts=[], values={"order": None})


@pytest.mark.anyio
async def test_stream_forwards_recommendation_preview_before_final_node_update() -> None:
    frames = [frame async for frame in _stream(RecommendationPreviewGraph(), {}, {}, is_resume=False)]

    preview_index = next(index for index, frame in enumerate(frames) if "event: recommendation_preview" in frame)
    final_index = next(index for index, frame in enumerate(frames) if '"node": "recommend"' in frame)

    assert preview_index < final_index
    assert "根据您的需求，为你推荐1款候选" in frames[preview_index]
