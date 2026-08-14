"""Unit tests: Plugin/Client pick up JWT credentials and the socket path from
env vars (R5-05). No live kernel required — connection methods are
monkeypatched."""
import asyncio

import pytest

from veyron import VeyronClient
from veyron.plugin import Plugin
from veyron.veyron_protocol_pb2 import Envelope, PluginRegisterAck


class _NoopPlugin(Plugin):
    plugin_id = "env-test-plugin"

    async def on_message(self, envelope):
        return None


class _FakeClient:
    def __init__(self):
        self.register_args = None

    async def register_full(self, plugin_id, version, manifest, jwt_token):
        self.register_args = (plugin_id, version, manifest, jwt_token)
        return PluginRegisterAck(accepted=True)

    async def recv(self):
        env = Envelope()
        env.plugin_shutdown.SetInParent()
        return env

    async def send(self, target, envelope):
        pass

    async def ack_event(self, event_id):
        pass

    async def close(self):
        pass


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in ("VEYRON_JWT_TOKEN", "VEYRON_JWT_SECRET", "VEYRON_SOCKET_PATH"):
        monkeypatch.delenv(var, raising=False)


def test_connect_from_env_reads_socket_path_and_secret(monkeypatch):
    monkeypatch.setenv("VEYRON_SOCKET_PATH", "/tmp/veyron-env.sock")
    monkeypatch.setenv("VEYRON_JWT_SECRET", "shh-secret")
    captured = {}

    async def fake_cws(cls, socket_path, secret):
        captured["socket_path"] = socket_path
        captured["secret"] = secret
        return _FakeClient()

    monkeypatch.setattr(VeyronClient, "connect_with_secret", classmethod(fake_cws))
    asyncio.run(VeyronClient.connect_from_env())
    assert captured == {"socket_path": "/tmp/veyron-env.sock", "secret": b"shh-secret"}


def test_connect_from_env_no_secret_passes_none(monkeypatch):
    monkeypatch.setenv("VEYRON_SOCKET_PATH", "/tmp/veyron-env.sock")
    captured = {}

    async def fake_cws(cls, socket_path, secret):
        captured["socket_path"] = socket_path
        captured["secret"] = secret
        return _FakeClient()

    monkeypatch.setattr(VeyronClient, "connect_with_secret", classmethod(fake_cws))
    asyncio.run(VeyronClient.connect_from_env())
    assert captured == {"socket_path": "/tmp/veyron-env.sock", "secret": None}


def test_run_with_passes_env_token_and_secret_through(monkeypatch):
    monkeypatch.setenv("VEYRON_JWT_TOKEN", "tok-123")
    monkeypatch.setenv("VEYRON_JWT_SECRET", "shh-secret")

    fake = _FakeClient()
    captured = {}

    async def fake_cws(cls, socket_path, secret):
        captured["socket_path"] = socket_path
        captured["secret"] = secret
        return fake

    monkeypatch.setattr(VeyronClient, "connect_with_secret", classmethod(fake_cws))

    plugin = _NoopPlugin()
    asyncio.run(plugin.run_with("/tmp/veyron-env.sock"))

    assert captured["socket_path"] == "/tmp/veyron-env.sock"
    assert captured["secret"] == b"shh-secret"
    # register_full called with id/version/manifest/jwt_token
    assert fake.register_args[0] == "env-test-plugin"
    assert fake.register_args[1] == "1.0.0"
    assert fake.register_args[3] == "tok-123"
