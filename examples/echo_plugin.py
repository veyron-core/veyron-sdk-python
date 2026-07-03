"""Lightweight demo plugin for the Veyron Python SDK.

Shows: lifecycle hooks, manifest declaration, action handling,
event subscription, and emitting events back to the kernel.

Run (with a kernel listening on the default socket):
    VEYRON_JWT_TOKEN=<token> python -m examples.echo_plugin
"""
import asyncio
import json

from veyron import Plugin
from veyron.veyron_protocol_pb2 import (
    ActionResponse,
    ActionStatus,
    Envelope,
    Event,
    PluginManifest,
)


class EchoPlugin(Plugin):
    plugin_id = "echo-plugin"
    manifest = PluginManifest(
        actions=["echo"],
        events=["system.low_memory"],
    )

    async def on_init(self) -> None:
        print(f"[{self.plugin_id}] registered, subscribing to events")
        await self._client.subscribe(list(self.manifest.events))

    async def on_message(self, envelope: Envelope) -> None:
        kind = envelope.WhichOneof("payload")

        if kind == "action_request":
            await self._handle_action(envelope)
        elif kind == "event":
            await self._handle_event(envelope)
        else:
            print(f"[{self.plugin_id}] unhandled message: {kind}")

    async def _handle_action(self, envelope: Envelope) -> None:
        req = envelope.action_request
        if req.action != "echo":
            resp = ActionResponse(
                action_id=req.action_id,
                status=ActionStatus.ACTION_NOT_FOUND,
                error=f"unknown action: {req.action}",
            )
        else:
            params = json.loads(req.params_json or b"{}")
            resp = ActionResponse(
                action_id=req.action_id,
                status=ActionStatus.ACTION_OK,
                data_json=json.dumps({"echo": params}).encode(),
            )
        out = Envelope(sender_id=self.plugin_id)
        out.action_response.CopyFrom(resp)
        await self._client.send("kernel", out)

    async def _handle_event(self, envelope: Envelope) -> None:
        evt = envelope.event
        print(f"[{self.plugin_id}] event {evt.event_type}: {evt.payload_json}")

    async def on_shutdown(self) -> None:
        print(f"[{self.plugin_id}] shutting down")


async def main() -> None:
    await EchoPlugin().run()


if __name__ == "__main__":
    asyncio.run(main())
