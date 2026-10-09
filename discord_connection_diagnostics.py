"""Passive connection diagnostics: no retries, state changes or raw payload logs."""
from __future__ import annotations

import asyncio
import math
import time


def gateway_heartbeat_latency(client):
    """Regular SDK heartbeats bypass socket_raw_send; use the SDK's own RTT."""
    try:
        value = float(client.latency)
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None
    return value if math.isfinite(value) and value >= 0 else None


def voice_connection_snapshot(voice, state=None, websocket=None):
    """Only numeric IDs, connection flags and SDK state; never tokens/endpoints."""
    state = state if state is not None else getattr(voice, "_connection", None)
    websocket = websocket if websocket is not None else getattr(state, "ws", None)
    socket = getattr(websocket, "ws", None)
    code = getattr(socket, "close_code", None)
    flow = getattr(state, "state", None)
    flow_name = getattr(flow, "name", "unknown")
    channel_id = getattr(getattr(voice, "channel", None), "id", None)
    generation = getattr(voice, "authority_generation", None)
    runner = getattr(state, "_runner", None)
    connector = getattr(state, "_connector", None)
    return {
        "channel_id": channel_id if isinstance(channel_id, int) else "unknown",
        "generation": generation if isinstance(generation, int) else "unknown",
        "sdk_state": flow_name if isinstance(flow_name, str) and flow_name.isidentifier() else "unknown",
        "sdk_reconnect": int(bool(getattr(state, "reconnect", False))),
        "sdk_reader_active": int(isinstance(runner, asyncio.Future) and not runner.done()),
        "sdk_connector_active": int(isinstance(connector, asyncio.Future) and not connector.done()),
        "expected_disconnect": int(bool(getattr(state, "_expecting_disconnect", False))),
        "close_code": code if isinstance(code, int) else "unknown",
    }


class VoiceConnectionDiagnostics:
    """Observe a single owned SDK connection and re-raise its original exceptions."""
    def __init__(self, voice, state, write):
        self.voice = voice
        self.state = state
        self.write = write

    def record(self, event, *, exception=None, websocket=None, **details):
        # Diagnostics must never prevent the original connect/disconnect flow.
        try:
            fields = voice_connection_snapshot(self.voice, self.state, websocket)
            reason = str(getattr(self.voice, "boss_disconnect_reason", "") or "")
            fields["program_reason"] = reason if reason.isidentifier() else "none"
            if exception is not None:
                fields["exception_type"] = type(exception).__name__
                code = getattr(exception, "code", None)
                if isinstance(code, int):
                    fields["close_code"] = code
                fields["failure_kind"] = (
                    "timeout" if isinstance(exception, TimeoutError) else
                    "socket_closed" if isinstance(code, int) else
                    "transport_error" if isinstance(exception, OSError) else "sdk_error"
                )
            # Only primitive, explicitly supplied metadata. Exception messages
            # and websocket payloads (which can contain secrets) are not copied.
            for key, value in details.items():
                if isinstance(value, (bool, int)):
                    fields[key] = int(value)
                elif isinstance(value, float) and math.isfinite(value):
                    fields[key] = f"{value:.3f}"
            self.write(event + " " + " ".join(f"{key}={value}" for key, value in fields.items()))
        except Exception:
            pass

    def install(self):
        connect_websocket = getattr(self.state, "_connect_websocket", None)
        if not callable(connect_websocket):
            self.record("voice_diagnostic_unavailable")
            return

        async def observed_connect_websocket(*args, **kwargs):
            resume = kwargs.get("resume", args[0] if args else False)
            self.record("voice_sdk_websocket_connect_start", resume=bool(resume))
            started_at = time.monotonic()
            try:
                websocket = await connect_websocket(*args, **kwargs)
            except asyncio.CancelledError:
                self.record("voice_sdk_websocket_connect_cancelled")
                raise
            except Exception as exc:
                self.record("voice_sdk_websocket_connect_failed", exception=exc)
                raise
            self.record("voice_sdk_websocket_opened", websocket=websocket,
                        connect_sec=time.monotonic() - started_at, resume=bool(resume))
            poll_event = getattr(websocket, "poll_event", None)
            if not callable(poll_event):
                self.record("voice_diagnostic_unavailable")
                return websocket

            async def observed_poll_event(*poll_args, **poll_kwargs):
                try:
                    return await poll_event(*poll_args, **poll_kwargs)
                except asyncio.CancelledError:
                    self.record("voice_sdk_reader_cancelled", websocket=websocket)
                    raise
                except Exception as exc:
                    self.record("voice_sdk_websocket_interrupted", exception=exc, websocket=websocket)
                    raise

            try:
                websocket.poll_event = observed_poll_event
            except (AttributeError, TypeError):
                self.record("voice_diagnostic_unavailable")
            return websocket

        try:
            self.state._connect_websocket = observed_connect_websocket
        except (AttributeError, TypeError):
            self.record("voice_diagnostic_unavailable")
