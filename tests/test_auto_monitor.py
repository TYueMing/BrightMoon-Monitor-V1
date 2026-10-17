from pathlib import Path
import sys
from unittest.mock import Mock, patch

import pytest

from monitor.codex_monitor.discovery import ServerDiscovery, listener_from_arguments
from monitor.codex_monitor.adapter import CodexSource
from monitor.src.source_config import normalize_sources
from test_codex_monitor import connected, approval, choose, seed


@pytest.mark.parametrize("args,expected", [
    (["codex.exe", "app-server", "--listen", "ws://127.0.0.1:4500"], "ws://127.0.0.1:4500"),
    (["C:/With Space/codex.exe", "app-server", "--listen=ws://localhost:8000"], "ws://localhost:8000"),
    (["codex", "app-server", "--listen", "stdio://"], ""),
    (["codex", "app-server", "--listen", "ws://0.0.0.0:4500"], ""),
    (["codex", "--remote", "ws://127.0.0.1:4500"], ""),
    (["other.exe", "app-server", "--listen", "ws://127.0.0.1:4500"], ""),
    (["codex", "app-server", "--listen", "ws://127.0.0.1:4500", "--ws-auth", "token"], ""),
    (["codex", "app-server", "--listen=ws://127.0.0.1:4500", "--ws-auth=token"], ""),
])
def test_only_explicit_local_unauthenticated_codex_listener_is_discovered(args, expected):
    assert listener_from_arguments(args) == expected


def test_discovery_is_cached_bounded_and_transient_failure_preserves_connections():
    reader = Mock(return_value=[(i, ["codex", "app-server", "--listen", f"ws://127.0.0.1:{4500+i}"]) for i in range(8)])
    discovery = ServerDiscovery(reader)
    assert len(discovery.poll()) == 3
    discovery.poll()
    reader.assert_called_once()
    reader.side_effect = OSError("PRIVATE error detail")
    discovery.next_scan = 0
    assert len(discovery.poll()) == 3 and "PRIVATE" not in discovery.message
    reader.side_effect = None
    reader.return_value = []
    discovery.next_scan = 0
    assert discovery.poll() == {}


def test_auto_default_and_legacy_manual_url_migrate_without_losing_user_choice():
    assert normalize_sources()["codex"]["connection_mode"] == "auto"
    config = {"codex": {"enabled": True, "server_url": "ws://localhost:4500"}}
    assert normalize_sources(config)["codex"]["connection_mode"] == "manual"
    config["codex"]["connection_mode"] = "logs"
    assert normalize_sources(config)["codex"]["connection_mode"] == "logs"
    config["codex"]["connection_mode"] = "invalid"
    with pytest.raises(ValueError):
        normalize_sources(config, strict=True)


def test_enable_before_codex_starts_automatically_picks_up_new_logs(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    source = CodexSource({})
    assert source.poll()["runs"] == []
    root = tmp_path/"sessions"
    root.mkdir()
    seed(root/"rollout-test.jsonl")
    result = source.poll()
    assert len(result["runs"]) == 1 and result["read_only"]
    assert result["runs"][0]["status"] == "running"
    source.close()


def test_auto_discovery_connects_routes_approval_and_disposes_on_restart(tmp_path):
    source = CodexSource({"data_path": str(tmp_path), "connection_mode": "auto"})
    client, _ = connected()
    client.handle(approval())
    client.poll = Mock(side_effect=client.snapshot)
    source.discovery = Mock(message="")
    endpoint = "ws://127.0.0.1:4500"
    source.discovery.poll.return_value = {endpoint: 1}
    with patch("monitor.codex_monitor.adapter.AppServerClient", return_value=client) as factory:
        result = source.poll()
        assert not result["read_only"] and len(result["actions"]) == 2
        source.poll()
        factory.assert_called_once()
        action, payload = choose(client)
        with patch.object(client, "send", wraps=client.send) as send:
            source.send(action["type"], action["run_id"], payload)
            send.assert_called_once()
        source.discovery.poll.return_value = {}
        source.poll()
        assert client.closed and not source.auto_clients
        with pytest.raises(ValueError):
            source.send(action["type"], action["run_id"], payload)
        source.close()
        assert source.poll()["status"] == "disabled"


def test_logs_mode_never_discovers_or_connects(tmp_path):
    with patch("monitor.codex_monitor.adapter.ServerDiscovery") as discover, patch("monitor.codex_monitor.adapter.AppServerClient") as client:
        source = CodexSource({"data_path": str(tmp_path), "connection_mode": "logs", "server_url": "ws://localhost:4500"})
        source.poll()
        discover.assert_not_called()
        client.assert_not_called()
        source.close()
