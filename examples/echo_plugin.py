"""Lightweight demo plugin for the Veyron Python SDK.

Shows: lifecycle hooks, manifest declaration, action handling (plain,
streaming, and publish-from-plugin), event subscription, and SessionClose
dispatch.

Run (with a kernel listening on the default socket):
    VEYRON_JWT_TOKEN=<token> python -m examples.echo_plugin
"""
import asyncio
import json

from veyron import Plugin
from veyron.veyron_protocol_pb2 import (
    ActionResponse,
    ActionResponseChunk,
    ActionStatus,
    Envelope,
    Event,
    EventPublishStatus,
    PluginManifest,
)


class EchoPlugin(Plugin):
    plugin_id = "echo-plugin"
    manifest = PluginManifest(
        permissions=["PERMISSION_EVENT_PUBLISH"],
        actions=["echo", "stream_echo", "publish_test"],
        events=["system.low_memory"],
    )

    def __init__(self) -> None:
        super().__init__()
        self._stream_action_id = None
        self._stream_chunks: dict[int, bytes] = {}

    async def on_init(self) -> None:
        print(f"[{self.plugin_id}] registered, subscribing to events")
        await self._client.subscribe(list(self.manifest.events))

    async def on_message(self, envelope: Envelope) -> None:
        kind = envelope.WhichOneof("payload")

        if kind == "action_request":
            await self._handle_action(envelope)
        elif kind == "action_request_chunk":
            await self._handle_request_chunk(envelope.action_request_chunk)
        elif kind == "event":
            await self._handle_event(envelope)
        elif kind == "session_close":
            # Proves the subprocess correctly discriminates SessionClose
            # from ActionStreamAbort over the real wire (P7-03 unit tests
            # already cover the discrimination logic itself).
            print(f"session_closed:{envelope.session_close.reason}", flush=True)
        else:
            print(f"[{self.plugin_id}] unhandled message: {kind}")

    async def _handle_action(self, envelope: Envelope) -> None:
        req = envelope.action_request
        if req.action == "stream_echo" and req.streaming:
            # Accept the session immediately, before any chunks arrive.
            # The kernel only honors SessionClose once the provider has
            # sent an accepting ActionResponse{OK} for this streaming
            # action (PendingAction::session_accepted) — see
            # src/plugins/registry.rs resolve_action_response.
            self._stream_action_id = req.action_id
            self._stream_chunks = {}
            accept = Envelope(sender_id=self.plugin_id)
            accept.action_response.CopyFrom(
                ActionResponse(action_id=req.action_id, status=ActionStatus.ACTION_OK)
            )
            await self._client.send("kernel", accept)
            return
        if req.action == "publish_test":
            await self._handle_publish_test(req)
            return

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

    async def _handle_request_chunk(self, chunk) -> None:
        """Accumulates chunks by seq until `final`, then replies with 2
        ActionResponseChunks (request bytes split roughly in half)
        followed by a terminal ActionResponse. In-memory, one streaming
        action at a time — sufficient for a round-trip test, not a
        general pattern."""
        if chunk.action_id != self._stream_action_id:
            return
        self._stream_chunks[chunk.seq] = chunk.chunk
        if not chunk.final:
            return

        full = b"".join(self._stream_chunks[seq] for seq in sorted(self._stream_chunks))
        mid = len(full) // 2
        halves = (full[:mid], full[mid:])
        for seq, part in enumerate(halves):
            rc = Envelope(sender_id=self.plugin_id)
            rc.action_response_chunk.CopyFrom(
                ActionResponseChunk(action_id=self._stream_action_id, seq=seq, chunk=part)
            )
            await self._client.send("kernel", rc)

        out = Envelope(sender_id=self.plugin_id)
        out.action_response.CopyFrom(
            ActionResponse(
                action_id=self._stream_action_id,
                status=ActionStatus.ACTION_OK,
                data_json=full,
            )
        )
        await self._client.send("kernel", out)

        self._stream_action_id = None
        self._stream_chunks = {}

    async def _handle_publish_test(self, req) -> None:
        try:
            ack = await self._client.publish_event("test_publish", req.params_json, 0)
            if ack.status == EventPublishStatus.EVENT_PUBLISH_OK:
                resp = ActionResponse(action_id=req.action_id, status=ActionStatus.ACTION_OK)
            else:
                resp = ActionResponse(
                    action_id=req.action_id,
                    status=ActionStatus.ACTION_ERROR,
                    error=ack.error,
                )
        except Exception as e:
            resp = ActionResponse(
                action_id=req.action_id, status=ActionStatus.ACTION_ERROR, error=str(e)
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
