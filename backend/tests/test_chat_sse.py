import json
from types import SimpleNamespace

import pytest

from app.api.chat import _stream


class MemoryEventGraph:
    async def astream(self, _payload, _cfg, stream_mode: str):
        assert stream_mode == "updates"
        yield {
            "load_memory": {
                "profile_preferences": {"preferred_colors": ["秘密黑"]},
                "memory_events": [{"type": "memory_read", "keys": ["preferred_colors"], "count": 1}],
            }
        }
        yield {
            "capture_memory": {
                "profile_preferences": {"preferred_platforms": ["秘密淘宝"]},
                "memory_events": [{"type": "memory_write", "action": "write", "keys": ["preferred_platforms"]}],
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
    assert "秘密淘宝" not in "".join(frames)
