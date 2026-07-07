import asyncio
import time
from typing import Optional

from .framing import (
    FLAG_FRAGMENTED,
    FLAG_MAC_PRESENT,
    FRAG_HEADER_SIZE,
    MAX_PAYLOAD,
    async_read_frame,
    derive_session_key,
    pack_frag_header,
    pack_frame,
    parse_frag_header,
)
from .veyron_protocol_pb2 import (
    Envelope,
    EventAck,
    PluginManifest,
    PluginRegister,
    Ping,
    Subscribe,
)

# Mirror of the kernel's inbound reassembly bounds (see src/ipc/connection.rs).
MAX_REASSEMBLY_STREAMS = 64
REASSEMBLY_TIMEOUT = 30.0


class _ReassemblyBuf:
    __slots__ = ("fragments", "total", "flags", "first_seen", "buffered_bytes")

    def __init__(self, total: int, flags: int):
        self.fragments: dict[int, bytes] = {}
        self.total = total
        self.flags = flags
        self.first_seen = time.monotonic()
        self.buffered_bytes = 0

    def is_complete(self) -> bool:
        return len(self.fragments) == self.total

    def reassemble(self) -> bytes:
        return b"".join(self.fragments[seq] for seq in range(self.total))


class VeyronClient:
    """Async client for the Veyron kernel IPC protocol."""

    def __init__(self, socket_path: str, secret: Optional[bytes] = None):
        self.socket_path = socket_path
        self._secret = secret
        self.session_key: Optional[bytes] = None
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self.plugin_id: Optional[str] = None
        self._reassembly: dict[int, _ReassemblyBuf] = {}
        self._next_stream_id = 1

    def _apply_session_nonce(self, plugin_id: str, nonce: bytes) -> None:
        """Derive and store session_key from a registration nonce."""
        if self._secret and nonce:
            self.session_key = derive_session_key(self._secret, nonce, plugin_id)

    async def connect(self) -> None:
        self._reader, self._writer = await asyncio.open_unix_connection(self.socket_path)

    async def close(self) -> None:
        if self._writer:
            self._writer.close()
            await self._writer.wait_closed()

    async def register(
        self,
        plugin_id: str,
        manifest: Optional[PluginManifest] = None,
        jwt_token: str = "",
    ) -> Envelope:
        self.plugin_id = plugin_id
        reg = PluginRegister(plugin_id=plugin_id, jwt_token=jwt_token)
        if manifest is not None:
            reg.manifest.CopyFrom(manifest)
        env = Envelope()
        env.plugin_register.CopyFrom(reg)
        # Registration frame is always CRC-only (session_key not yet derived)
        await self._send_envelope("kernel", env, force_no_mac=True)
        ack = await self.recv(force_no_mac=True)
        if ack.HasField("plugin_register_ack"):
            # session_nonce was added in proto v1.2; use getattr for forward compat.
            nonce = getattr(ack.plugin_register_ack, "session_nonce", b"")
            if nonce:
                self._apply_session_nonce(plugin_id, nonce)
        return ack

    async def send(self, target: str, envelope: Envelope) -> None:
        await self._send_envelope(target, envelope)

    async def recv_frame(self, force_no_mac: bool = False):
        """Receive the next complete frame as (flags, payload), transparently
        reassembling FLAG_FRAGMENTED frames. Mirrors the rust SDK's
        VeyronClient::recv_frame."""
        key = None if force_no_mac else self.session_key
        while True:
            self._prune_reassembly()
            flags, payload = await async_read_frame(self._reader, session_key=key)
            if flags & FLAG_FRAGMENTED:
                complete = self._absorb_fragment(flags, payload)
                if complete is None:
                    continue
                return complete
            return flags, payload

    async def recv(self, force_no_mac: bool = False) -> Envelope:
        _flags, payload = await self.recv_frame(force_no_mac=force_no_mac)
        env = Envelope()
        env.ParseFromString(payload)
        return env

    async def subscribe(self, event_types: list) -> None:
        sub = Subscribe(event_types=event_types)
        env = Envelope()
        env.subscribe.CopyFrom(sub)
        await self._send_envelope("kernel", env)

    async def ack_event(self, event_id: str) -> None:
        """Confirm an Event was received and handled — kernel stops retrying
        it. An un-acked event is redelivered up to max_retries then dropped
        (T-06)."""
        env = Envelope()
        env.event_ack.CopyFrom(EventAck(event_id=event_id))
        await self._send_envelope("kernel", env)

    async def ping(self) -> float:
        ts = int(time.time() * 1000)
        ping_msg = Ping(timestamp=ts)
        env = Envelope()
        env.ping.CopyFrom(ping_msg)
        t0 = time.monotonic()
        await self._send_envelope("kernel", env)
        await self.recv()
        return time.monotonic() - t0

    async def send_fragmented(self, target: str, payload: bytes, chunk_size: int) -> None:
        """Split `payload` into FLAG_FRAGMENTED frames of at most `chunk_size`
        data bytes each and send them on a fresh stream id. The kernel
        reassembles them into a single logical frame for `target`.

        Bounds mirror the kernel: total payload <= 1 MiB, <= 65535 fragments."""
        if len(payload) > MAX_PAYLOAD:
            raise ValueError(f"payload too large: {len(payload)} > {MAX_PAYLOAD}")
        if chunk_size <= 0 or chunk_size + FRAG_HEADER_SIZE > MAX_PAYLOAD:
            raise ValueError(f"invalid fragment chunk_size: {chunk_size}")
        total = max(1, -(-len(payload) // chunk_size))  # ceil div
        if total > 0xFFFF:
            raise ValueError(f"payload needs {total} fragments; max is 65535")

        stream_id = self._next_stream_id
        self._next_stream_id = (self._next_stream_id + 1) & 0xFFFFFFFF or 1
        fragment_id = stream_id & 0xFFFF

        for seq in range(total):
            chunk = payload[seq * chunk_size : (seq + 1) * chunk_size]
            frag_payload = pack_frag_header(fragment_id, seq, total, stream_id) + chunk
            await self._send_raw_with_flags(target, FLAG_FRAGMENTED, frag_payload)

    def _prune_reassembly(self) -> None:
        """Stale sets can't pin memory forever."""
        now = time.monotonic()
        stale = [sid for sid, buf in self._reassembly.items() if now - buf.first_seen >= REASSEMBLY_TIMEOUT]
        for sid in stale:
            del self._reassembly[sid]

    def _absorb_fragment(self, flags: int, payload: bytes):
        """Buffer one fragment; returns (flags, payload) when the set is
        complete, else None. Mirrors the rust SDK's absorb_fragment."""
        hdr = parse_frag_header(payload)
        if hdr is None:
            raise ValueError("fragment header too short")
        _fragment_id, seq, total, stream_id = hdr
        if total == 0 or seq >= total:
            raise ValueError(f"invalid fragment header: seq {seq} / total {total}")

        buf = self._reassembly.get(stream_id)
        if buf is not None:
            if buf.total != total:
                del self._reassembly[stream_id]
                raise ValueError("fragment total mismatch within stream")
        elif len(self._reassembly) >= MAX_REASSEMBLY_STREAMS:
            raise ValueError("too many concurrent fragment streams")
        else:
            buf = _ReassemblyBuf(total, flags & ~(FLAG_FRAGMENTED | FLAG_MAC_PRESENT))
            self._reassembly[stream_id] = buf

        chunk = payload[FRAG_HEADER_SIZE:]
        replaced_len = len(buf.fragments.get(seq, b""))
        new_total = buf.buffered_bytes - replaced_len + len(chunk)
        if new_total > MAX_PAYLOAD:
            del self._reassembly[stream_id]
            raise ValueError(f"reassembled payload too large: {new_total} > {MAX_PAYLOAD}")
        buf.buffered_bytes = new_total
        buf.fragments[seq] = chunk

        if buf.is_complete():
            del self._reassembly[stream_id]
            return buf.flags, buf.reassemble()
        return None

    async def _send_envelope(
        self,
        target: str,
        envelope: Envelope,
        force_no_mac: bool = False,
    ) -> None:
        payload = envelope.SerializeToString()
        key = None if force_no_mac else self.session_key
        frame = pack_frame(target, payload, session_key=key)
        self._writer.write(frame)
        await self._writer.drain()

    async def _send_raw_with_flags(self, target: str, extra_flags: int, payload: bytes) -> None:
        frame = pack_frame(target, payload, flags=extra_flags, session_key=self.session_key)
        self._writer.write(frame)
        await self._writer.drain()
