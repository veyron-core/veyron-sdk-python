"""Unit tests: Plugin base-class serve loop answers the kernel watchdog Ping
with a Pong (AUDIT H-02). No live kernel required — the client is faked."""
import asyncio

from vynkor.plugin import Plugin
from vynkor.vynkor_protocol_pb2 import (
    Envelope,
    Ping,
    PluginRegisterAck,
    PluginShutdown,
)


class _FakeClient:
    """Stands in for VynkorClient: hands out a scripted inbound envelope
    sequence and records everything the plugin sends."""

    def __init__(self, inbound):
        self._inbound = list(inbound)
        self.sent = []  # list of (target, Envelope)
        self.closed = False

    async def register_full(self, plugin_id, version, manifest, jwt_token):
        return PluginRegisterAck(accepted=True)

    async def recv(self):
        return self._inbound.pop(0)

    async def send(self, target, envelope):
        self.sent.append((target, envelope))

    async def ack_event(self, event_id):
        self.sent.append(("kernel", Envelope(event_ack={"event_id": event_id})))


class _RecordingPlugin(Plugin):
    plugin_id = "ping-test-plugin"

    def __init__(self):
        super().__init__()
        self.seen = []
        self.events_seen = []

    async def on_message(self, envelope):
        self.seen.append(envelope)
        return None

    async def on_event(self, event):
        self.events_seen.append(event)
        return None

    def sent_pongs(self):
        return [(t, e) for (t, e) in self._client.sent if e.HasField("pong")]

    def sent_event_acks(self):
        return [(t, e) for (t, e) in self._client.sent if e.HasField("event_ack")]


def _ping_env(timestamp):
    env = Envelope()
    env.ping.timestamp = timestamp
    return env


def _shutdown_env():
    env = Envelope()
    env.plugin_shutdown.SetInParent()
    return env


def _run(plugin, inbound, jwt_token=""):
    fake = _FakeClient(inbound)
    asyncio.run(plugin.serve(fake, jwt_token))
    return fake


def test_run_loop_answers_ping_with_pong():
    plugin = _RecordingPlugin()
    _run(plugin, [_ping_env(123456), _shutdown_env()])

    assert len(plugin.sent_pongs()) == 1
    target, pong_env = plugin.sent_pongs()[0]
    assert target == "kernel"
    assert pong_env.pong.original_timestamp == 123456
    assert pong_env.pong.server_timestamp > 0


def test_ping_is_not_dispatched_to_on_message():
    plugin = _RecordingPlugin()
    _run(plugin, [_ping_env(1), _shutdown_env()])
    assert plugin.seen == []


def test_non_ping_non_event_messages_still_reach_on_message():
    subscribe_env = Envelope()
    subscribe_env.subscribe.event_types.append("*")
    plugin = _RecordingPlugin()
    _run(plugin, [_ping_env(1), subscribe_env, _shutdown_env()])

    assert len(plugin.seen) == 1
    assert plugin.seen[0].HasField("subscribe")
    assert len(plugin.sent_pongs()) == 1


def test_events_dispatched_to_on_event_and_acked_not_on_message():
    event_env = Envelope()
    event_env.event.event_id = "evt-1"
    plugin = _RecordingPlugin()
    _run(plugin, [_ping_env(1), event_env, _shutdown_env()])

    assert plugin.seen == []
    assert len(plugin.events_seen) == 1
    assert plugin.events_seen[0].event_id == "evt-1"
    acks = plugin.sent_event_acks()
    assert len(acks) == 1
    assert acks[0][1].event_ack.event_id == "evt-1"
