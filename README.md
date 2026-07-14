# veyron-sdk

Python SDK for writing [Veyron](https://github.com/veyron-core/veyron) plugins.

A Veyron plugin is a separate OS process supervised by the Veyron kernel. It
talks to the kernel over a Unix domain socket using the Veyron wire protocol:
framed messages carrying Protobuf envelopes, with optional zstd compression,
HMAC-SHA256 frame authentication, and fragmentation.

## Protocol source

`proto/veyron_protocol.proto` is vendored from
[`veyron-wire`](https://crates.io/crates/veyron-wire)'s `wire/proto/`. It's
copied by hand, not path-referenced — re-sync it when the protocol changes
upstream, then regenerate `veyron/veyron_protocol_pb2.py`.

## Install

```bash
pip install veyron-sdk
```

## Quick start

```python
import asyncio
import json

from veyron import Plugin
from veyron.veyron_protocol_pb2 import ActionResponse, ActionStatus, Envelope, PluginManifest


class EchoPlugin(Plugin):
    plugin_id = "echo-plugin"
    manifest = PluginManifest(actions=["echo"])

    async def on_message(self, envelope: Envelope) -> None:
        if envelope.WhichOneof("payload") != "action_request":
            return
        req = envelope.action_request
        resp = ActionResponse(
            action_id=req.action_id,
            status=ActionStatus.ACTION_OK,
            data_json=json.dumps({"echo": json.loads(req.params_json or b"{}")}).encode(),
        )
        out = Envelope(sender_id=self.plugin_id)
        out.action_response.CopyFrom(resp)
        await self._client.send("kernel", out)


if __name__ == "__main__":
    asyncio.run(EchoPlugin().run())
```

`Plugin.run` connects, registers, and serves until the kernel asks the plugin
to shut down. The SDK answers `Ping` automatically and exits the loop on
`PluginShutdown`. See `examples/echo_plugin.py` for a fuller example with
event subscription.

## Environment

| Variable             | Meaning                                                        |
|----------------------|-----------------------------------------------------------------|
| `VEYRON_SOCKET_PATH` | Kernel UDS path. Default: `XDG_RUNTIME_DIR` → `/run/user/<uid>` → `~/.veyron/run` (never shared `/tmp`). |
| `VEYRON_JWT_TOKEN`   | JWT presented at registration (required on secured kernels).   |
| `VEYRON_JWT_SECRET`  | Shared secret; enables per-frame HMAC-SHA256 tags after registration. |

## Protocol coverage

The SDK's `framing` module implements the full Veyron wire format described
in `docs/FRAMING.md`: HMAC-tagged frames, zstd decompression for payloads
compressed by the kernel, and reassembly of fragmented messages.

## Client API

For lower-level control, use `VeyronClient` directly:

```python
client = VeyronClient(socket_path, secret=secret)
await client.connect()
ack = await client.register("weather", manifest, jwt_token)

await client.subscribe(["alarm.fired"])
ack = await client.publish_event("weather.updated", b'{"city":"Berlin"}', 5_000)
latency = await client.ping()

resp = await client.send_action("get_weather", b'{"city":"Berlin"}', 5_000)

action_id = await client.send_action_streaming("transcribe", 30_000)
await client.send_request_chunk(action_id, 0, b"hi", True)
await client.send_response_chunk(action_id, 0, b"ok")
await client.close_session(action_id, "done")
```

`publish_event` requires `PERMISSION_EVENT_PUBLISH`; `timeout_ms=0` uses the
kernel's 30s default. It returns the kernel's `EventPublishAck` as-is —
inspect `ack.status` yourself (`EVENT_PUBLISH_OK`/`ERROR`/`PERMISSION_DENY`) —
and only raises on a kernel `Error` envelope or on timeout. Requests and
responses are matched on a single connection; drive request/response traffic
from one task.

`send_action` follows the same `timeout_ms=0` → 30s-default convention and
returns the kernel's `ActionResponse` as-is (inspect `.status` yourself). It
raises `RuntimeError` on a kernel `Error` envelope or an `ActionStreamAbort`
for this `action_id`, and `TimeoutError` on deadline expiry.
`send_action_streaming` fires an `ActionRequest(streaming=True)` and returns
its generated `action_id` immediately, without waiting for any response —
drive `recv()`/chunks yourself afterward. `send_request_chunk`,
`send_response_chunk`, and `close_session` are fire-and-forget sends (no
response awaited); `close_session` has no `final` flag — the response side
of a stream is terminated by an ordinary `ActionResponse`.

## Development

```bash
pip install -e ".[dev]"
pytest
```

## License

MIT
