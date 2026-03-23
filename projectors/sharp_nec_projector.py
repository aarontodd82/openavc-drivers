"""
OpenAVC Sharp NEC Projector Driver.

Controls Sharp NEC projectors via TCP using the NEC binary control protocol.
Default port: 7142. Compatible with most NEC and Sharp NEC projector models
including P, PA, PE, PV, PX, ME, and M series.

Protocol reference:
  https://assets.sharpnecdisplays.us/documents/miscellaneous/sharp-pj-control-command-codes.pdf

Packet format (command):
    [02h] [CMD] [00h] [00h] [DATA_LEN] [DATA...] [CHECKSUM]

Packet format (query):
    [03h] [CMD] [00h] [00h] [DATA_LEN] [DATA...] [CHECKSUM]

Packet format (response):
    [22h/23h] [CMD] [MODEL_HI] [MODEL_LO] [DATA_LEN] [DATA...] [CHECKSUM]

Checksum = sum of all preceding bytes & 0xFF.
Minimum 600ms between commands per NEC specification.

Features beyond PJLink:
- Direct input selection by source type (HDMI, DP, HDBaseT, VGA)
- Separate picture mute and sound mute control
- Light source / lamp hours monitoring
- Model-agnostic binary protocol (works across Sharp NEC product lines)

Tested on: Sharp NEC PE456 series.
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from server.drivers.base import BaseDriver
from server.transport.frame_parsers import CallableFrameParser, FrameParser
from server.utils.logger import get_logger

log = get_logger(__name__)

# --- Protocol constants ---

HEADER_CMD = 0x02       # Control command header
HEADER_QUERY = 0x03     # Query command header
RESP_CMD_OK = 0x22      # Successful control response
RESP_QUERY_OK = 0x23    # Successful query response
RESP_CMD_ERR = 0xA2     # Failed control response
RESP_QUERY_ERR = 0xA3   # Failed query response

VALID_RESP_HEADERS = {RESP_CMD_OK, RESP_QUERY_OK, RESP_CMD_ERR, RESP_QUERY_ERR}

# Control command codes (CMD byte, used with HEADER_CMD)
CMD_POWER_ON = 0x00
CMD_POWER_OFF = 0x01
CMD_INPUT_SELECT = 0x03
CMD_PICTURE_MUTE_ON = 0x10
CMD_PICTURE_MUTE_OFF = 0x11
CMD_SOUND_MUTE_ON = 0x12
CMD_SOUND_MUTE_OFF = 0x13

# Query command codes (CMD byte, used with HEADER_QUERY)
CMD_INFO_REQUEST = 0x8A  # Returns 98-byte projector status block

# Input source codes (second data byte in INPUT_SELECT command)
INPUT_CODES: dict[str, int] = {
    "computer": 0x01,
    "hdmi1": 0x1A,
    "hdmi2": 0x1B,
    "displayport": 0xA6,
    "hdbaset": 0xBF,
    # Aliases for convenience
    "vga": 0x01,
    "dp": 0xA6,
}

# Canonical names for state reporting (no aliases)
INPUT_NAMES: dict[int, str] = {
    0x01: "computer",
    0x1A: "hdmi1",
    0x1B: "hdmi2",
    0xA6: "displayport",
    0xBF: "hdbaset",
}

# Minimum inter-command delay per NEC spec
MIN_CMD_DELAY = 0.6


def _checksum(data: bytes) -> int:
    """NEC checksum: sum of all bytes, masked to 8 bits."""
    return sum(data) & 0xFF


def _build_packet(header: int, cmd: int, data: bytes = b"") -> bytes:
    """Build a NEC command packet.

    Format: [header] [cmd] [00] [00] [data_len] [data...] [checksum]
    Model code bytes are 00 00 (broadcast / any model).
    """
    body = bytes([header, cmd, 0x00, 0x00, len(data)]) + data
    return body + bytes([_checksum(body)])


def _parse_nec_frame(buffer: bytes) -> tuple[bytes | None, bytes]:
    """Extract one NEC response frame from a byte buffer.

    Returns (frame_bytes_without_checksum, remaining_buffer)
    or (None, buffer) if no complete frame is available yet.
    """
    # Scan for a valid response header
    start = -1
    for i, b in enumerate(buffer):
        if b in VALID_RESP_HEADERS:
            start = i
            break

    if start == -1:
        return None, b""

    if start > 0:
        buffer = buffer[start:]

    # Minimum frame: header(1) + cmd(1) + model(2) + len(1) + checksum(1) = 6
    if len(buffer) < 6:
        return None, buffer

    data_len = buffer[4]
    total_len = 5 + data_len + 1  # 5 header bytes + data + checksum

    if len(buffer) < total_len:
        return None, buffer

    frame = buffer[: total_len - 1]
    expected_cs = _checksum(frame)
    actual_cs = buffer[total_len - 1]

    if expected_cs != actual_cs:
        log.warning(
            f"NEC checksum mismatch: expected 0x{expected_cs:02X}, "
            f"got 0x{actual_cs:02X}, skipping byte"
        )
        return None, buffer[1:]

    return frame, buffer[total_len:]


class SharpNECProjectorDriver(BaseDriver):
    """Sharp NEC binary protocol driver for projectors."""

    DRIVER_INFO = {
        "id": "sharp_nec_projector",
        "name": "Sharp NEC Projector",
        "manufacturer": "Sharp NEC",
        "category": "projector",
        "version": "1.0.0",
        "author": "OpenAVC",
        "description": (
            "Controls Sharp NEC projectors via the NEC binary control "
            "protocol over TCP. Compatible with P, PA, PE, PV, PX, ME, "
            "and M series projectors."
        ),
        "transport": "tcp",
        "help": {
            "overview": (
                "Controls Sharp NEC projectors using the proprietary binary "
                "protocol on TCP port 7142. Provides deeper control than "
                "PJLink — dedicated input selection, separate picture and "
                "sound mute, and light source hours monitoring."
            ),
            "setup": (
                "1. Connect the projector to the network\n"
                "2. Enable LAN control in the projector's network settings\n"
                "3. Assign a static IP address to the projector\n"
                "4. Default control port is 7142\n"
                "5. This driver can coexist with PJLink (port 4352)"
            ),
        },
        "discovery": {
            "ports": [7142],
            "mac_prefixes": [
                "00:e0:63",  # NEC Corporation
                "00:c2:c6",  # NEC Corporation
                "00:30:13",  # NEC Corporation
            ],
        },
        "default_config": {
            "host": "",
            "port": 7142,
            "poll_interval": 15,
        },
        "config_schema": {
            "host": {
                "type": "string",
                "required": True,
                "label": "IP Address",
            },
            "port": {
                "type": "integer",
                "default": 7142,
                "label": "Port",
            },
            "poll_interval": {
                "type": "integer",
                "default": 15,
                "min": 0,
                "label": "Poll Interval (sec)",
                "help": "How often to query projector status. 0 to disable.",
            },
        },
        "state_variables": {
            "power": {
                "type": "enum",
                "values": ["off", "on", "warming", "cooling"],
                "label": "Power State",
            },
            "input": {
                "type": "string",
                "label": "Input Source",
            },
            "picture_mute": {
                "type": "boolean",
                "label": "Picture Mute",
                "help": "True when the projected image is blanked.",
            },
            "sound_mute": {
                "type": "boolean",
                "label": "Sound Mute",
                "help": "True when the built-in speaker is muted.",
            },
            "lamp_hours": {
                "type": "integer",
                "label": "Light Source Hours",
                "help": "Lamp or laser light source usage in hours.",
            },
        },
        "commands": {
            "power_on": {
                "label": "Power On",
                "params": {},
                "help": "Turn on the projector. May take 30-60 seconds to reach full brightness.",
            },
            "power_off": {
                "label": "Power Off",
                "params": {},
                "help": "Turn off the projector (standby).",
            },
            "set_input": {
                "label": "Set Input",
                "params": {
                    "input": {
                        "type": "enum",
                        "values": [
                            "hdmi1", "hdmi2", "computer",
                            "displayport", "hdbaset",
                        ],
                        "required": True,
                        "help": "Input source to select.",
                    },
                },
                "help": "Switch the projector's active input source.",
            },
            "picture_mute_on": {
                "label": "Picture Mute On",
                "params": {},
                "help": "Blank the projected image (light source stays active).",
            },
            "picture_mute_off": {
                "label": "Picture Mute Off",
                "params": {},
                "help": "Restore the projected image.",
            },
            "sound_mute_on": {
                "label": "Sound Mute On",
                "params": {},
                "help": "Mute the projector's built-in speaker.",
            },
            "sound_mute_off": {
                "label": "Sound Mute Off",
                "params": {},
                "help": "Unmute the projector's built-in speaker.",
            },
            "refresh": {
                "label": "Refresh Status",
                "params": {},
                "help": "Query all projector status immediately.",
            },
        },
    }

    def __init__(
        self,
        device_id: str,
        config: dict[str, Any],
        state: "StateStore",
        events: "EventBus",
    ):
        self._last_cmd_time: float = 0.0
        self._transition_task: asyncio.Task | None = None
        super().__init__(device_id, config, state, events)

    # --- Transport hooks ---

    def _create_frame_parser(self) -> Optional[FrameParser]:
        return CallableFrameParser(_parse_nec_frame)

    def _resolve_delimiter(self) -> Optional[bytes]:
        return None  # Binary framing, no delimiter

    # --- Internal helpers ---

    async def _send(self, header: int, cmd: int, data: bytes = b"") -> None:
        """Send a NEC command, enforcing inter-command delay."""
        if not self.transport or not self.transport.connected:
            raise ConnectionError(f"[{self.device_id}] Not connected")

        loop = asyncio.get_event_loop()
        elapsed = loop.time() - self._last_cmd_time
        if elapsed < MIN_CMD_DELAY:
            await asyncio.sleep(MIN_CMD_DELAY - elapsed)

        packet = _build_packet(header, cmd, data)
        await self.transport.send(packet)
        self._last_cmd_time = loop.time()
        log.debug(f"[{self.device_id}] TX: {packet.hex(' ')}")

    def _start_transition_monitor(self) -> None:
        """Poll power status during warming/cooling transitions."""
        if self._transition_task and not self._transition_task.done():
            self._transition_task.cancel()

        async def _monitor() -> None:
            try:
                for _ in range(30):  # Up to ~90s with delays
                    await asyncio.sleep(3.0)
                    power = self.get_state("power")
                    if power in ("on", "off"):
                        log.info(
                            f"[{self.device_id}] Power transition complete: "
                            f"{power}"
                        )
                        return
                    await self._send(HEADER_QUERY, CMD_INFO_REQUEST)
                log.warning(
                    f"[{self.device_id}] Power transition monitor timed out"
                )
            except (asyncio.CancelledError, ConnectionError):
                pass

        self._transition_task = asyncio.create_task(_monitor())

    # --- Connection lifecycle ---

    async def connect(self) -> None:
        await super().connect()
        try:
            await self._send(HEADER_QUERY, CMD_INFO_REQUEST)
        except ConnectionError:
            log.warning(f"[{self.device_id}] Initial status query failed")

    async def disconnect(self) -> None:
        if self._transition_task and not self._transition_task.done():
            self._transition_task.cancel()
            try:
                await self._transition_task
            except asyncio.CancelledError:
                pass
            self._transition_task = None
        await super().disconnect()

    # --- Command interface ---

    async def send_command(
        self, command: str, params: dict[str, Any] | None = None
    ) -> Any:
        params = params or {}

        match command:
            case "power_on":
                self.set_state("power", "warming")
                await self._send(HEADER_CMD, CMD_POWER_ON)
                self._start_transition_monitor()

            case "power_off":
                self.set_state("power", "cooling")
                await self._send(HEADER_CMD, CMD_POWER_OFF)
                self._start_transition_monitor()

            case "set_input":
                input_name = params.get("input", "").lower()
                code = INPUT_CODES.get(input_name)
                if code is not None:
                    await self._send(
                        HEADER_CMD, CMD_INPUT_SELECT,
                        bytes([0x01, code]),
                    )
                    self.set_state("input", INPUT_NAMES.get(code, input_name))
                else:
                    log.warning(
                        f"[{self.device_id}] Unknown input '{input_name}'. "
                        f"Valid: {', '.join(INPUT_NAMES.values())}"
                    )

            case "picture_mute_on":
                await self._send(HEADER_CMD, CMD_PICTURE_MUTE_ON)
                self.set_state("picture_mute", True)

            case "picture_mute_off":
                await self._send(HEADER_CMD, CMD_PICTURE_MUTE_OFF)
                self.set_state("picture_mute", False)

            case "sound_mute_on":
                await self._send(HEADER_CMD, CMD_SOUND_MUTE_ON)
                self.set_state("sound_mute", True)

            case "sound_mute_off":
                await self._send(HEADER_CMD, CMD_SOUND_MUTE_OFF)
                self.set_state("sound_mute", False)

            case "refresh":
                await self.poll()

            case _:
                log.warning(f"[{self.device_id}] Unknown command: {command}")

    # --- Response parsing ---

    async def on_data_received(self, data: bytes) -> None:
        if len(data) < 5:
            return

        header = data[0]
        cmd = data[1]
        data_len = data[4]
        payload = data[5 : 5 + data_len] if data_len > 0 else b""

        if header == RESP_CMD_OK:
            log.debug(f"[{self.device_id}] ACK cmd=0x{cmd:02X}")

        elif header == RESP_QUERY_OK:
            if cmd == CMD_INFO_REQUEST:
                self._parse_info_response(payload)
            else:
                log.debug(
                    f"[{self.device_id}] Query OK cmd=0x{cmd:02X} "
                    f"({data_len} bytes)"
                )

        elif header in (RESP_CMD_ERR, RESP_QUERY_ERR):
            log.warning(
                f"[{self.device_id}] Error response cmd=0x{cmd:02X}: "
                f"{data.hex(' ')}"
            )

    def _parse_info_response(self, payload: bytes) -> None:
        """Parse the information request response (up to 98 bytes).

        Byte positions are 0-indexed (DATA01 in the spec = index 0).

        Known positions:
        - [82..85] (DATA83-86): Light source usage in seconds (big-endian)

        Power status byte position varies by model generation. The first
        byte is logged so you can identify the mapping for your hardware.
        """
        if not payload:
            return

        # Log first bytes for protocol debugging on new models
        preview = payload[:8].hex(" ") if len(payload) >= 8 else payload.hex(" ")
        log.debug(f"[{self.device_id}] Info response ({len(payload)} bytes): {preview} ...")

        # Light source hours: DATA83-86 = seconds as 32-bit big-endian
        if len(payload) >= 86:
            source_seconds = (
                (payload[82] << 24)
                | (payload[83] << 16)
                | (payload[84] << 8)
                | payload[85]
            )
            source_hours = source_seconds // 3600
            old_hours = self.get_state("lamp_hours")
            self.set_state("lamp_hours", source_hours)
            if source_hours != old_hours:
                log.info(f"[{self.device_id}] Light source: {source_hours}h")

        # Power status from first data byte (common mapping, may vary)
        # Known values across models:
        #   0x00/0x01 = standby, 0x04 = on, 0x05 = cooling, 0x06 = standby+error
        power_byte = payload[0]
        power_map: dict[int, str] = {
            0x00: "off",
            0x01: "off",
            0x04: "on",
            0x05: "cooling",
            0x06: "off",
        }
        new_power = power_map.get(power_byte)
        if new_power is not None:
            old_power = self.get_state("power")
            # Don't override warming/cooling with on/off prematurely
            if old_power in ("warming", "cooling") and new_power == old_power:
                pass  # Let transition monitor handle it
            else:
                self.set_state("power", new_power)
                if new_power != old_power:
                    log.info(f"[{self.device_id}] Power: {new_power}")
        else:
            log.info(
                f"[{self.device_id}] Unknown power byte 0x{power_byte:02X} "
                f"— check protocol docs for this model"
            )

    # --- Polling ---

    async def poll(self) -> None:
        if not self.transport or not self.transport.connected:
            return
        try:
            await self._send(HEADER_QUERY, CMD_INFO_REQUEST)
        except ConnectionError:
            log.warning(f"[{self.device_id}] Poll failed — not connected")
