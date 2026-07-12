from pathlib import Path

import pytest

from mercury_tools.mercury_runtime import skill_markdown

ROOT = Path(__file__).resolve().parents[1]
BUNDLED_SKILLS = (
    "company-health-check-th",
    "connector-credential-setup-th",
    "connector-setup-guide-th",
    "flowaccount-connector-setup-th",
    "flowaccount-journal-posting-th",
    "invoice-review-th",
    "management-report-th",
    "mercury-flow-runner",
    "peak-connector-setup-th",
    "vat-summary-th",
)


@pytest.fixture(autouse=True)
def explicit_runtime_source_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MERCURY_TOOLS_ROOT", str(ROOT))


@pytest.mark.parametrize("skill_name", BUNDLED_SKILLS)
def test_bundled_skill_is_available_to_mcp_runtime(skill_name: str) -> None:
    markdown = skill_markdown(skill_name)

    assert markdown is not None
    assert f"name: {skill_name}" in markdown


def test_bundled_setup_skills_use_local_credential_commands() -> None:
    for skill_name in (
        "connector-credential-setup-th",
        "connector-setup-guide-th",
        "flowaccount-connector-setup-th",
        "peak-connector-setup-th",
    ):
        markdown = skill_markdown(skill_name)

        assert markdown is not None
        assert "credential_status" in markdown
        assert "mercury credentials setup" in markdown
        assert "mercury credentials test" in markdown
        assert "workspace_id" not in markdown


def test_bundled_journal_skill_uses_generic_local_write_tools() -> None:
    markdown = skill_markdown("flowaccount-journal-posting-th")

    assert markdown is not None
    assert "preview_erp_write" in markdown
    assert "confirm_erp_write" in markdown
    assert "execute_erp_write" in markdown
    assert "create_flowaccount_journal_draft" not in markdown


def test_skill_markdown_honors_an_explicit_runtime_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skill_dir = tmp_path / "plugins" / "mercury-finance" / "skills" / "demo-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# Demo Skill\n", encoding="utf-8")
    monkeypatch.setenv("MERCURY_TOOLS_ROOT", str(tmp_path))

    assert skill_markdown("demo-skill") == "# Demo Skill\n"
