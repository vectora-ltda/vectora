from backend.services.context_compaction import compact_messages
from backend.vtypes.message import MessageRole, text_message


def test_compact_messages_preserves_system_and_latest_turn() -> None:
    messages = [
        text_message(MessageRole.SYSTEM, "rules"),
        text_message(MessageRole.USER, "old objective " * 30),
        text_message(MessageRole.ASSISTANT, "old answer " * 30),
        text_message(MessageRole.USER, "current objective"),
    ]

    result = compact_messages(messages, max_tokens=20)

    assert result[0].role is MessageRole.SYSTEM
    assert result[-1].text() == "current objective"
    assert any("Context compacted" in message.text() for message in result)


def test_compact_messages_is_disabled_without_mutating_history() -> None:
    messages = [text_message(MessageRole.USER, "hello " * 50)]

    assert compact_messages(messages, max_tokens=1, enabled=False) is messages
