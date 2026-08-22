# veyron-sdk

Python SDK for writing [Veyron](https://github.com/vynkor-core/vynkor) plugins.

A Veyron plugin is a separate OS process supervised by the Veyron kernel. It
talks to the kernel over a Unix domain socket using the Veyron wire protocol:
framed messages carrying Protobuf envelopes, with optional zstd compression,
HMAC-SHA256 frame authentication, and fragmentation.

## Protocol source

`proto/veyron_protocol.proto` is vendored from
[`vynkor-wire`](https://crates.io/crates/vynkor-wire)'s `proto/` (wire
protocol **v1.6** as of the latest sync). It's copied by hand, not
path-referenced — re-sync it when the protocol changes upstream, then
regenerate `veyron/vynkor_protocol_pb2.py` (the kernel's
`scripts/gen_proto_python.py` does both; the kernel's R8-05 test guards
byte identity).

## Install

```bash
pip install veyron-sdk
```

## Quick start

```python
import asyncio
import json

from vynkor import Plugin
from vynkor.vynkor_protocol_pb2 import ActionResponse, ActionStatus, Envelope, PluginManifest


class EchoPlugin(Plugin):
    def id(self) -> str:
        return "echo-plugin"

    def manifest(self) -> PluginManifest:
        return PluginManifest(actions=["echo"])

    async def on_message(self, envelope: Envelope) -> Envelope | None:
        if envelope.WhichOneof("payload") != "action_request":
            return None
        req = envelope.action_request
        resp = ActionResponse(
            action_id=req.action_id,
            status=ActionStatus.ACTION_OK,
            data_json=json.dumps({"echo": json.loads(req.params_json or b"{}")}).encode(),
        )
        out = Envelope(sender_id=self.id())
        out.action_response.CopyFrom(resp)
        return out  # auto-sent to "kernel" by serve()


if __name__ == "__main__":
    asyncio.run(EchoPlugin().run())
```

`Plugin.run` connects, registers, and serves until the kernel asks the plugin
to shut down. The SDK answers `Ping` automatically, acknowledges delivered
events after `on_event` succeeds, and exits the loop on `PluginShutdown`.

## Plugin model

The `Plugin` base class mirrors the Rust `Plugin` trait 1:1. Override the
methods you need:

| Method | Default | Notes |
|--------|---------|-------|
| `id() -> str` | required | unique plugin id, e.g. `"weather"` |
| `version() -> str` | `"1.0.0"` | semver reported at registration |
| `manifest() -> PluginManifest` | empty manifest | declared capabilities |
| `on_init(client)` | no-op | called once after registration, before the loop |
| `on_message(envelope) -> Envelope \| None` | `None` | return an envelope to reply to the kernel |
| `on_event(event) -> Envelope \| None` | `None` | auto-acked on success; raise to skip the ack |
| `on_shutdown()` | no-op | called once when the loop ends |

`run()` connects using `VYN_SOCKET_PATH` (or the per-user default),
`run_with(socket_path)` targets an explicit path, and
`serve(client, jwt_token)` registers on an existing client and runs the loop.
A handler error from `on_message` stops the loop (after `on_shutdown`) and is
re-raised out of `serve`.

## Environment

| Variable             | Meaning                                                        |
|----------------------|----------------------------------------------------------------|
| `VYN_SOCKET_PATH` | Kernel UDS path. Default: `XDG_RUNTIME_DIR` → `/run/user/<uid>` → `~/.local/state/vyn/run` (never shared `/tmp`). |
| `VYN_JWT_TOKEN`   | JWT presented at registration (required on secured kernels).   |
| `VYN_JWT_SECRET`  | Shared secret; enables per-frame HMAC-SHA256 tags after registration. |

## Protocol coverage

The SDK's `framing` module implements the full Veyron wire format described
in `docs/FRAMING.md`: HMAC-tagged frames, zstd compression for outbound
payloads ≥ 64 KiB and decompression of compressed inbound frames, and
reassembly of fragmented messages.

## Client API

For lower-level control, use `VynkorClient` directly:

```python
client = await VynkorClient.connect_with_secret(socket_path, secret)
ack = await client.register("weather", manifest)

await client.subscribe(["alarm.fired"])
ack = await client.publish_event("weather.updated", b'{"city":"Berlin"}', 5_000)
latency = await client.ping()  # round-trip in seconds

resp = await client.send_action("get_weather", b'{"city":"Berlin"}', 5_000)

action_id = await client.send_action_streaming("transcribe", 30_000)
await client.send_request_chunk(action_id, 0, b"hi", True)
await client.send_response_chunk(action_id, 0, b"ok")
await client.close_session(action_id, "done")
```

Constructors: `VynkorClient.connect(socket_path)`,
`VynkorClient.connect_with_secret(socket_path, secret)`, and
`VynkorClient.connect_from_env()` (reads `VYN_SOCKET_PATH` +
`VYN_JWT_SECRET`) all return a connected client. The classic
`VynkorClient(socket_path)` + `await client.connect()` pattern still works.
Registration variants: `register(plugin_id, manifest)` and
`register_full(plugin_id, version, manifest, jwt_token)` return the typed
`PluginRegisterAck` (inspect `.accepted` / `.reject_reason`), not the raw
`Envelope`.

`publish_event` requires `PERMISSION_EVENT_PUBLISH`; `timeout_ms=0` uses the
kernel's 30s default. It returns the kernel's `EventPublishAck` as-is —
inspect `ack.status` yourself (`EVENT_PUBLISH_OK`/`ERROR`/`PERMISSION_DENY`) —
and only raises on a kernel `Error` envelope or on timeout. Requests and
responses are matched on a single connection; drive request/response traffic
from one task.

`send_action` follows the same `timeout_ms=0` → 30s-default convention and
returns the kernel's `ActionResponse` as-is (inspect `.status` yourself). It
raises `VynkorError` on a kernel `Error` envelope or an `ActionStreamAbort`
for this `action_id`, and `VeyronTimeout` on deadline expiry.
`send_action_streaming` fires an `ActionRequest(streaming=True)` and returns
its generated `action_id` immediately, without waiting for any response —
drive `recv()`/chunks yourself afterward. `send_request_chunk`,
`send_response_chunk`, and `close_session` are fire-and-forget sends (no
response awaited); `close_session` has no `final` flag — the response side of
a stream is terminated by an ordinary `ActionResponse`.

Other client methods: `recv()` / `recv_frame()` / `recv_timeout(timeout)`,
`subscribe` / `unsubscribe`, `ack_event`, `send_command` (returns
`KernelCommandAck`), `send_audio_chunk`, `send_raw_audio`,
`send_fragmented`, `is_secured()`.

## Errors

All SDK-level failures raise `vynkor.VynkorError` (or a subclass) instead of
bare `ValueError` / `RuntimeError` / `TimeoutError`. Subclasses mirror the
Rust `WireError` variants: `VeyronIoError`, `VeyronProtoError`,
`VeyronFrameMagicMismatch`, `VeyronFrameCrcMismatch`, `VeyronFrameReadTimeout`,
`VeyronPayloadTooLarge`, `VeyronTimeout`, `VeyronPermissionDenied`,
`VeyronInternal`.

## Development

```bash
pip install -e ".[dev]"
pytest
```

The test suite lives in `tests/` and imports the in-tree package directly
(`pythonpath = ["."]` in `pyproject.toml`), so no install is needed to run
it. `tests/test_sdk.py` requires a live kernel socket and is skipped when
absent; the rest use fake clients / socketpairs.

## License

MIT
