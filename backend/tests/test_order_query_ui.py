from pathlib import Path


ROOT = Path(__file__).parents[2]
CHAT = ROOT / "frontend" / "src" / "components" / "ChatView.vue"
STYLES = ROOT / "frontend" / "src" / "style.css"
ROBOT = ROOT / "frontend" / "public" / "robot-avatar.svg"


def test_order_query_message_has_robot_avatar_and_full_width_scrollable_card():
    chat = CHAT.read_text(encoding="utf-8")
    styles = STYLES.read_text(encoding="utf-8")

    assert 'class="avatar"' in chat
    assert 'src="/robot-avatar.svg"' in chat
    assert "order-query-bubble" in chat
    assert ".order-query-bubble" in styles
    assert "max-width: none" in styles
    assert ".order-query-list" in styles
    assert "max-height:" in styles
    assert "overflow-y: auto" in styles
    assert ROBOT.exists()
