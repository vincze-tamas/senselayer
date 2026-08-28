import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts" / "senselayer_supervisor.py"
SPEC = importlib.util.spec_from_file_location("supervisor", SCRIPT)
supervisor = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(supervisor)


def test_tunnel_is_fail_closed_and_loopback_only():
    command = supervisor.tunnel_command({
        "ssh_host": "example.test",
        "ssh_user": "senselayer",
        "identity_file": "key",
        "known_hosts_file": "known_hosts",
        "local_receiver_port": 18787,
        "local_dashboard_port": 18501,
    })
    joined = " ".join(command)
    assert "BatchMode=yes" in joined
    assert "StrictHostKeyChecking=yes" in joined
    assert "ExitOnForwardFailure=yes" in joined
    assert "127.0.0.1:18787:127.0.0.1:8787" in command
    assert "127.0.0.1:18501:127.0.0.1:8501" in command
    assert "0.0.0.0" not in joined
