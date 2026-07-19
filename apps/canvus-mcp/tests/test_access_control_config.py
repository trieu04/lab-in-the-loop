"""Environment-backed static authorization configuration tests."""

from __future__ import annotations

from pydantic import SecretStr

from canvus_mcp.config import Settings


def test_static_tokens_are_secret_strings_and_canvas_maps_are_operator_owned(monkeypatch) -> None:
    monkeypatch.setenv("CANVUS_API_URL", "https://canvus.example/api/v1")
    monkeypatch.setenv("CANVUS_API_KEY", "sdk-secret")
    monkeypatch.setenv("CANVUS_MCP_READER_TOKEN", "reader-secret")
    monkeypatch.setenv("CANVUS_MCP_READER_CANVASES", '["canvas-a"]')
    monkeypatch.setenv("CANVUS_CANVAS_CLASSIFICATIONS", '{"canvas-a":"internal"}')
    settings = Settings()
    assert isinstance(settings.mcp_reader_token, SecretStr)
    assert settings.mcp_reader_token.get_secret_value() == "reader-secret"
    assert settings.mcp_reader_canvases == ["canvas-a"]
    assert settings.canvas_classifications == {"canvas-a": "internal"}
    assert "reader-secret" not in repr(settings)


def test_invalid_canvas_classification_json_fails_closed(monkeypatch) -> None:
    monkeypatch.setenv("CANVUS_API_URL", "https://canvus.example/api/v1")
    monkeypatch.setenv("CANVUS_API_KEY", "sdk-secret")
    monkeypatch.setenv("CANVUS_CANVAS_CLASSIFICATIONS", "not-json")
    assert Settings().canvas_classifications == {}
