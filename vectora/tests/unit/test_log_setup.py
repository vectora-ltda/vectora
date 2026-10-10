import json
import logging

from backend.services.log_setup import JSONFormatter, TextFormatter


def test_text_formatter_exposes_safe_tool_context_and_redacts_credentials() -> None:
    record = logging.LogRecord(
        "backend.tools.fs",
        logging.INFO,
        __file__,
        1,
        "terminal_command_executed",
        (),
        None,
    )
    record.command = "gh api --header 'Authorization: Bearer super-secret'"
    record.exit_code = 0
    record.unlisted_secret = "must not be emitted"

    rendered = TextFormatter().format(record)

    assert "command=\"gh api --header 'Authorization: Bearer [REDACTED]'\"" in rendered
    assert "exit_code=0" in rendered
    assert "super-secret" not in rendered
    assert "unlisted_secret" not in rendered


def test_json_formatter_emits_safe_context_without_raw_token() -> None:
    record = logging.LogRecord(
        "backend.tools.github",
        logging.INFO,
        __file__,
        1,
        "github_api_request_started",
        (),
        None,
    )
    record.endpoint = "https://api.github.com/repos/o/r?access_token=secret"
    record.repo = "r"
    record.authorization = "Bearer secret"

    payload = json.loads(JSONFormatter().format(record))

    assert (
        payload["endpoint"]
        == "https://api.github.com/repos/o/r?access_token=%5BREDACTED%5D"
    )
    assert payload["repo"] == "r"
    assert "authorization" not in payload
    assert "secret" not in json.dumps(payload)
