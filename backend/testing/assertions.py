"""Asserções de teste sobre `VMessage` (`backend/vtypes/message.py`)."""

from typing import Any

from backend.vtypes.message import MessageRole, VMessage


def assert_tool_called(
    messages: list[VMessage],
    tool_name: str,
) -> None:
    """Assert that a specific tool was called in the message sequence.

    Args:
        messages: List of messages from a conversation turn
        tool_name: Name of the tool to verify was called

    Raises:
        AssertionError: If tool was not called
    """
    for msg in messages:
        if msg.role == MessageRole.ASSISTANT and msg.tool_calls:
            for tool_call in msg.tool_calls:
                if tool_call.name == tool_name:
                    return

    msg_0 = f"Tool '{tool_name}' was not called. Messages: {[m.role.value for m in messages]}"
    raise AssertionError(msg_0)


def assert_tool_called_with_args(
    messages: list[VMessage],
    tool_name: str,
    expected_args: dict[str, Any],
) -> None:
    """Assert that a specific tool was called with expected arguments.

    Args:
        messages: List of messages from a conversation turn
        tool_name: Name of the tool
        expected_args: Expected arguments dict

    Raises:
        AssertionError: If tool was not called with those args
    """
    for msg in messages:
        if msg.role == MessageRole.ASSISTANT and msg.tool_calls:
            for tool_call in msg.tool_calls:
                if tool_call.name == tool_name:
                    if tool_call.args == expected_args:
                        return
                    msg_0 = (
                        f"Tool '{tool_name}' called with {tool_call.args}, "
                        f"expected {expected_args}"
                    )
                    raise AssertionError(msg_0)

    msg_0 = f"Tool '{tool_name}' with args {expected_args} was not found in message sequence"
    raise AssertionError(msg_0)


def assert_tool_result_in_messages(
    messages: list[VMessage],
    tool_name: str,
    expected_result: Any,
) -> None:
    """Assert that a tool's result appears in the message sequence.

    Args:
        messages: List of messages from a conversation turn
        tool_name: Name of the tool
        expected_result: Expected result (will be converted to string)

    Raises:
        AssertionError: If result not found
    """
    expected_str = str(expected_result)

    for msg in messages:
        if msg.role == MessageRole.TOOL and msg.name == tool_name:
            if expected_str in msg.text():
                return

    msg_0 = f"Result '{expected_result}' from tool '{tool_name}' not found in messages"
    raise AssertionError(msg_0)


def assert_message_contains_text(
    messages: list[VMessage],
    text: str,
) -> None:
    """Assert that any message contains specific text.

    Args:
        messages: List of messages
        text: Text to find

    Raises:
        AssertionError: If text not found in any message
    """
    for msg in messages:
        if text in msg.text():
            return

    msg_0 = (
        f"Text '{text}' not found in any message. "
        f"Messages: {[m.text()[:50] for m in messages]}"
    )
    raise AssertionError(msg_0)


def assert_last_message_is_ai(messages: list[VMessage]) -> VMessage:
    """Assert that the last message is from the assistant.

    Args:
        messages: List of messages

    Returns:
        The last assistant VMessage

    Raises:
        AssertionError: If last message is not from the assistant
    """
    if not messages:
        msg = "No messages in sequence"
        raise AssertionError(msg)

    if messages[-1].role != MessageRole.ASSISTANT:
        msg = f"Last message is {messages[-1].role.value}, expected assistant"
        raise AssertionError(msg)

    return messages[-1]
