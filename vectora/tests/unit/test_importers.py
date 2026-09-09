from __future__ import annotations

from backend.services.importers import preview_mcp_config, preview_skill_config


def test_preview_mcp_config_keeps_env_names_without_values() -> None:
    result = preview_mcp_config(
        {
            "mcpServers": {
                "demo": {
                    "command": "npx",
                    "args": ["-y", "demo"],
                    "env": {"TOKEN": "secret-value"},
                }
            }
        }
    )
    assert result["valid"] == [
        {
            "name": "demo",
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "demo"],
            "url": "",
            "env_vars": ["TOKEN"],
        }
    ]
    assert "secret-value" not in str(result)


def test_preview_mcp_config_redacts_credentials_query_and_fragment() -> None:
    result = preview_mcp_config(
        {
            "mcpServers": {
                "remote": {
                    "url": "https://token:secret@example.test/mcp?api_key=value#private",
                }
            }
        }
    )

    assert result["valid"][0]["url"] == "https://example.test/mcp"
    assert "secret" not in str(result)
    assert "api_key" not in str(result)


def test_preview_skill_config_reports_invalid_entries_without_installing() -> None:
    result = preview_skill_config(
        {
            "skills": [
                {"name": "Review", "source": "https://example.test/review.git"},
                {"name": "bad"},
            ]
        }
    )
    assert result["valid"][0]["id"] == "review"
    assert result["ignored"] == [{"reason": "name/source ausente"}]


def test_preview_skill_config_redacts_source_credentials_query_and_fragment() -> None:
    result = preview_skill_config(
        [
            {
                "name": "Review",
                "source": "https://token:secret@example.test/review.git?ref=private#readme",
            }
        ]
    )

    assert result["valid"][0]["source"] == "https://example.test/review.git"
    assert "secret" not in str(result)
    assert "ref=private" not in str(result)
