from pathlib import Path


INSTALLER = Path(__file__).parents[1] / "scripts" / "install_windows.ps1"


def test_installer_disables_task_scheduler_battery_restrictions():
    script = INSTALLER.read_text(encoding="utf-8")

    create_index = script.index("schtasks.exe /Create")
    disallow_index = script.index("$TaskSettings.DisallowStartIfOnBatteries = $false")
    stop_index = script.index("$TaskSettings.StopIfGoingOnBatteries = $false")

    assert create_index < disallow_index
    assert create_index < stop_index
    assert "Set-ScheduledTask -TaskName $TaskName -Settings $TaskSettings" in script