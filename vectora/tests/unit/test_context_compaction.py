from backend.services.context_compaction import compact_messages
from backend.vtypes.message import (
    ContentBlock,
    MessageRole,
    ToolCall,
    VMessage,
    text_message,
)


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


def test_compaction_keeps_tool_call_and_results_together() -> None:
    call = VMessage(
        role=MessageRole.ASSISTANT,
        tool_calls=[ToolCall(id="call-1", name="file_read", args={"path": "a"})],
    )
    result = VMessage(
        role=MessageRole.TOOL,
        tool_call_id="call-1",
        content=[ContentBlock(kind="text", text="file contents")],
    )
    messages = [
        text_message(MessageRole.USER, "old " * 100),
        call,
        result,
        text_message(MessageRole.USER, "latest"),
    ]

    compacted = compact_messages(messages, max_tokens=24)

    assert [message.role for message in compacted[-3:]] == [
        MessageRole.ASSISTANT,
        MessageRole.TOOL,
        MessageRole.USER,
    ]
    assert compacted[-2].tool_call_id == "call-1"
