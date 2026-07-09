from mercury_tools.mercury_runtime import skill_markdown


def test_bundled_peak_skill_is_available_to_mcp_runtime() -> None:
    markdown = skill_markdown("peak-connector-setup-th")

    assert markdown is not None
    assert "PEAK Connector Setup TH" in markdown
    assert "ConnectId" in markdown
    assert "read capabilities only" in markdown
    assert "workspace_id" in markdown
    assert "validate_connector_connection" in markdown


def test_bundled_flowaccount_skill_is_available_to_mcp_runtime() -> None:
    markdown = skill_markdown("flowaccount-connector-setup-th")

    assert markdown is not None
    assert "FlowAccount Connector Setup TH" in markdown
    assert "client_id" in markdown
    assert "client_secret" in markdown
    assert "FlowAccount Endpoint Data Dictionary" in markdown
    assert "validate_connector_connection" in markdown


def test_skill_markdown_uses_explicit_runtime_root(tmp_path, monkeypatch) -> None:
    skill_dir = tmp_path / "plugins" / "mercury-finance" / "skills" / "demo-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# Demo Skill\n", encoding="utf-8")

    monkeypatch.setenv("MERCURY_TOOLS_ROOT", str(tmp_path))

    assert skill_markdown("demo-skill") == "# Demo Skill\n"
