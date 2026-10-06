"""The CLI must import and run on every supported Python (a syntax error here once slipped past the suite)."""
import compileall
from pathlib import Path

import pytest

from tradebot import cli

SRC = Path(__file__).resolve().parents[1] / "src" / "tradebot"


def test_every_module_compiles():
    assert compileall.compile_dir(str(SRC), quiet=1, force=True)


def test_help_and_research_status_run(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["--help"])
    assert e.value.code == 0
    cli.main(["research", "status"])
    assert "2026 test runs so far" in capsys.readouterr().out


def test_offline_simulated_cycle_runs(tmp_path, monkeypatch, capsys):
    cfg = Path(__file__).resolve().parents[1] / "config" / "settings.yaml"
    text = cfg.read_text().replace("journal_path: data/journal.sqlite", f"journal_path: {tmp_path / 'j.sqlite'}")
    (tmp_path / "s.yaml").write_text(text)
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    cli.main(["--config", str(tmp_path / "s.yaml"), "--offline", "--mode", "simulated", "cycle"])
    assert '"kill_switch"' in capsys.readouterr().out
