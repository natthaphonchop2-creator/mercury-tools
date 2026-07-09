from mercury_tools.mercury_runtime import skill_markdown


def test_bundled_peak_skill_is_available_to_mcp_runtime() -> None:
    markdown = skill_markdown("peak-connector-setup-th")

    assert markdown is not None
    assert "PEAK Connector Setup TH" in markdown
    assert "ConnectId" in markdown
    assert "GET/POST" in markdown
    assert "validate_connector_connection" in markdown
