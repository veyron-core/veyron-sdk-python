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

## Development

```bash
pip install -e ".[dev]"
pytest
```

## License

MIT
