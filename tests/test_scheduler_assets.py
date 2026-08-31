from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
SYSTEMD_DIR = ROOT / "ops" / "systemd"


def test_incremental_service_invokes_only_official_cli() -> None:
    content = (
        SYSTEMD_DIR / "bulario-incremental.service.in"
    ).read_text(encoding="utf-8")

    assert "ExecStart=@PYTHON_BIN@ -m bulario_service.sync incremental" in content
    assert "--auto-resume" in content
    assert "--max-pages 5" in content
    assert "--max-products 20" in content
    assert "--headed" in content
    assert "reconcile" not in content
    assert "full" not in content


def test_incremental_timer_has_configurable_technical_default() -> None:
    content = (
        SYSTEMD_DIR / "bulario-incremental.timer"
    ).read_text(encoding="utf-8")

    assert "OnBootSec=5min" in content
    assert "OnUnitInactiveSec=1h" in content
    assert "Persistent=true" in content
    assert "RandomizedDelaySec=5min" in content
    assert "Unit=bulario-incremental.service" in content


def test_scheduler_scripts_have_valid_bash_syntax() -> None:
    for name in (
        "install-user-timer.sh",
        "uninstall-user-timer.sh",
    ):
        script = SYSTEMD_DIR / name
        result = subprocess.run(
            ["bash", "-n", str(script)],
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr


def test_installer_renders_current_project_and_virtualenv() -> None:
    content = (
        SYSTEMD_DIR / "install-user-timer.sh"
    ).read_text(encoding="utf-8")

    assert 'PROJECT_DIR="$(cd ' in content
    assert 'PYTHON_BIN="${PROJECT_DIR}/.venv/bin/python"' in content
    assert "systemctl --user daemon-reload" in content
    assert "enable --now bulario-incremental.timer" in content
    assert "import-environment DISPLAY" in content
