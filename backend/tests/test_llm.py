from app.agents.llm import LIGHT_MODEL, MAIN_MODEL


def test_model_tiering_uses_flash_for_lightweight_routing() -> None:
    assert LIGHT_MODEL == "qwen-flash"
    assert MAIN_MODEL == "qwen-plus"
