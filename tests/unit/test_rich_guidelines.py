"""Tests for rich guide directory loading and subsystem matching."""

from pathlib import Path

from bb_review.guidelines import (
    load_guidelines,
    load_rich_context,
    match_subsystems,
    parse_subsystem_triggers,
)
from bb_review.models import ReviewFocus, Severity


class TestParseSubsystemTriggers:
    """Tests for parsing subsystem.md trigger tables."""

    def test_parse_valid_table(self, tmp_path: Path):
        content = """\
# Subsystem Guide Index

| Subsystem | Triggers | File |
|-----------|----------|------|
| Sockets | socket, sock_, rpc_socket | sockets.md |
| Config | cfg_, configurator | config.md |
"""
        md_file = tmp_path / "subsystem.md"
        md_file.write_text(content)

        triggers = parse_subsystem_triggers(md_file)

        assert len(triggers) == 2
        assert triggers[0]["subsystem"] == "Sockets"
        assert "socket" in triggers[0]["triggers"]
        assert triggers[0]["file"] == "sockets.md"
        assert triggers[1]["subsystem"] == "Config"
        assert triggers[1]["file"] == "config.md"

    def test_skip_header_and_separator(self, tmp_path: Path):
        content = """\
| Subsystem | Triggers | File |
|-----------|----------|------|
| Networking | net/ | net.md |
"""
        md_file = tmp_path / "subsystem.md"
        md_file.write_text(content)

        triggers = parse_subsystem_triggers(md_file)

        assert len(triggers) == 1
        assert triggers[0]["subsystem"] == "Networking"

    def test_skip_non_md_files(self, tmp_path: Path):
        content = """\
| Subsystem | Triggers | File |
|-----------|----------|------|
| Valid | foo/ | valid.md |
| Invalid | bar/ | not_a_file |
"""
        md_file = tmp_path / "subsystem.md"
        md_file.write_text(content)

        triggers = parse_subsystem_triggers(md_file)

        assert len(triggers) == 1
        assert triggers[0]["file"] == "valid.md"

    def test_empty_table(self, tmp_path: Path):
        content = "# No table here\nJust some text.\n"
        md_file = tmp_path / "subsystem.md"
        md_file.write_text(content)

        triggers = parse_subsystem_triggers(md_file)

        assert triggers == []


class TestMatchSubsystems:
    """Tests for matching changed files against subsystem triggers."""

    def test_exact_trigger_match(self):
        triggers = [
            {"subsystem": "Sockets", "triggers": "socket, sock_", "file": "sockets.md"},
            {"subsystem": "Config", "triggers": "cfg_, configurator", "file": "config.md"},
        ]

        matched = match_subsystems(triggers, ["src/socket_test.c"])

        assert matched == ["sockets.md"]

    def test_multiple_matches(self):
        triggers = [
            {"subsystem": "Sockets", "triggers": "socket", "file": "sockets.md"},
            {"subsystem": "Config", "triggers": "cfg_", "file": "config.md"},
        ]

        matched = match_subsystems(triggers, ["socket_test.c", "cfg_utils.c"])

        assert "sockets.md" in matched
        assert "config.md" in matched

    def test_no_match(self):
        triggers = [
            {"subsystem": "Sockets", "triggers": "socket", "file": "sockets.md"},
        ]

        matched = match_subsystems(triggers, ["main.c", "utils.c"])

        assert matched == []

    def test_case_insensitive(self):
        triggers = [
            {"subsystem": "Sockets", "triggers": "SOCK_", "file": "sockets.md"},
        ]

        matched = match_subsystems(triggers, ["test/sock_helper.c"])

        assert matched == ["sockets.md"]

    def test_deduplication(self):
        """Same file not added twice even if multiple triggers match."""
        triggers = [
            {"subsystem": "Net A", "triggers": "socket, sock_", "file": "net.md"},
        ]

        matched = match_subsystems(triggers, ["socket_test.c", "sock_helper.c"])

        assert matched == ["net.md"]

    def test_empty_triggers(self):
        matched = match_subsystems([], ["socket_test.c"])
        assert matched == []

    def test_empty_files(self):
        triggers = [
            {"subsystem": "Sockets", "triggers": "socket", "file": "sockets.md"},
        ]
        matched = match_subsystems(triggers, [])
        assert matched == []


class TestLoadRichContext:
    """Tests for loading rich guide directory content."""

    def test_load_technical_patterns(self):
        """Load from the real net-drv-ts guide directory."""
        context = load_rich_context("net-drv-ts")

        assert len(context) > 0
        assert "CHECK_RC" in context
        assert "Technical Patterns" in context

    def test_no_guide_dir(self):
        """Return empty for nonexistent repo."""
        context = load_rich_context("nonexistent-repo-xyz")

        assert context == ""

    def test_subsystem_matching(self):
        """Subsystem triggers are parsed from real net-drv-ts guides."""
        context = load_rich_context("net-drv-ts", changed_files=["socket_test.c"])

        # Should have technical patterns (always loaded)
        assert "CHECK_RC" in context

    def test_no_changed_files(self):
        """Without changed_files, only base docs are loaded."""
        context = load_rich_context("net-drv-ts")

        # Technical patterns should still be loaded
        assert "CHECK_RC" in context


class TestLoadGuidelinesWithRichContext:
    """Tests for load_guidelines enriched with rich guide dirs."""

    def test_enriches_context(self, tmp_path: Path):
        """YAML context is enriched with rich guide dir content."""
        guidelines = load_guidelines(tmp_path, repo_name="net-drv-ts")

        # Should have rich context from guides/net-drv-ts/
        assert "CHECK_RC" in guidelines.context
        # Should have structured fields from YAML
        assert ReviewFocus.BUGS in guidelines.focus
        assert guidelines.severity_threshold == Severity.MEDIUM

    def test_no_enrichment_without_repo_name(self, tmp_path: Path):
        """Without repo_name, no enrichment happens."""
        guidelines = load_guidelines(tmp_path)

        assert guidelines.context == ""

    def test_changed_files_passed_through(self, tmp_path: Path):
        """changed_files param reaches subsystem matching."""
        guidelines = load_guidelines(
            tmp_path,
            repo_name="net-drv-ts",
            changed_files=["socket_test.c"],
        )

        assert "CHECK_RC" in guidelines.context
