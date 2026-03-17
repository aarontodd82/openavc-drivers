"""
OpenAVC PJLink Class 1 Driver.

Controls any PJLink Class 1 compatible projector (Epson, NEC, Panasonic,
Sony, Hitachi, Christie, and many others).

Protocol spec: https://pjlink.jbmia.or.jp/english/
TCP-based text protocol on port 4352.
Command format: %1<CMD> <param>\r
Response format: %1<CMD>=<response>\r
"""

from __future__ import annotations

import asyncio
from typing import Any

from server.drivers.base import BaseDriver
from server.transport.tcp import TCPTransport
from server.utils.logger import get_logger

log = get_logger(__name__)


class PJLinkDriver(BaseDriver):
    """PJLink Class 1 projector control driver."""

    DRIVER_INFO = {
        "id": "pjlink_class1",
        "name": "PJLink Class 1 Projector",
        "manufacturer": "Generic",
        "category": "projector",
        "version": "1.0.0",
        "author": "OpenAVC",
        "description": "Controls any PJLink Class 1 compatible projector.",
        "transport": "tcp",
        "default_config": {
            "host": "",
            "port": 4352,
            "password": "",
            "poll_interval": 15,
        },
        "config_schema": {
            "host": {"type": "string", "required": True, "label": "IP Address"},
            "port": {"type": "integer", "default": 4352, "label": "Port"},
            "password": {
                "type": "string",
                "default": "",
                "label": "Password",
                "secret": True,
            },
            "poll_interval": {
                "type": "integer",
                "default": 15,
                "min": 0,
                "label": "Poll Interval (sec)",
            },
        },
        "state_variables": {
            "power": {
                "type": "enum",
                "values": ["off", "on", "warming", "cooling"],
                "label": "Power State",
            },
            "input": {
                "type": "enum",
                "values": ["hdmi1", "hdmi2", "vga1", "vga2", "network"],
                "label": "Input",
            },
            "mute_video": {"type": "boolean", "label": "Video Mute"},
            "mute_audio": {"type": "boolean", "label": "Audio Mute"},
            "lamp_hours": {"type": "integer", "label": "Lamp Hours"},
            "error_status": {"type": "string", "label": "Error Status"},
        },
        "commands": {
            "power_on": {"label": "Power On", "params": {}},
            "power_off": {"label": "Power Off", "params": {}},
            "set_input": {
                "label": "Set Input",
                "params": {
                    "input": {
                        "type": "enum",
                        "values": ["hdmi1", "hdmi2", "vga1", "vga2", "network"],
                        "required": True,
                    }
                },
            },
            "mute_video": {"label": "Video Mute On", "params": {}},
            "unmute_video": {"label": "Video Mute Off", "params": {}},
            "mute_audio": {"label": "Audio Mute On", "params": {}},
            "unmute_audio": {"label": "Audio Mute Off", "params": {}},
        },
    }

    # PJLink input code mapping
    INPUT_MAP = {
        "hdmi1": "31",
        "hdmi2": "32",
        "vga1": "11",
        "vga2": "12",
        "network": "51",
    }
    INPUT_REVERSE = {v: k for k, v in INPUT_MAP.items()}

    # PJLink power state mapping
    POWER_MAP = {"0": "off", "1": "on", "2": "cooling", "3": "warming"}

    async def connect(self) -> None:
        """Connect to the projector via TCP and start polling."""
        host = self.config.get("host", "")
        port = self.config.get("port", 4352)

        self.transport = await TCPTransport.create(
            host=host,
            port=port,
            on_data=self.on_data_received,
            on_disconnect=self._handle_disconnect,
            delimiter=b"\r",
            timeout=5.0,
        )

        # Read PJLink greeting (e.g., "PJLINK 0" for no auth)
        # The greeting arrives as a regular message via on_data_received
        # Give it a moment to arrive
        await asyncio.sleep(0.1)

        self._connected = True
        self.set_state("connected", True)
        await self.events.emit(f"device.connected.{self.device_id}")
        log.info(f"[{self.device_id}] Connected to PJLink projector at {host}:{port}")

        # Start polling
        poll_interval = self.config.get("poll_interval", 15)
        if poll_interval > 0:
            await self.start_polling(poll_interval)

    async def disconnect(self) -> None:
        """Disconnect from the projector."""
        await self.stop_polling()
        if self.transport:
            await self.transport.close()
            self.transport = None
        self._connected = False
        self.set_state("connected", False)
        await self.events.emit(f"device.disconnected.{self.device_id}")
        log.info(f"[{self.device_id}] Disconnected")

    async def send_command(
        self, command: str, params: dict[str, Any] | None = None
    ) -> Any:
        """Send a named command to the projector."""
        params = params or {}

        if not self.transport or not self.transport.connected:
            raise ConnectionError(f"[{self.device_id}] Not connected")

        match command:
            case "power_on":
                await self.transport.send(b"%1POWR 1\r")
            case "power_off":
                await self.transport.send(b"%1POWR 0\r")
            case "set_input":
                input_name = params.get("input", "")
                input_code = self.INPUT_MAP.get(input_name)
                if input_code:
                    await self.transport.send(f"%1INPT {input_code}\r".encode())
                else:
                    log.warning(f"[{self.device_id}] Unknown input: {input_name}")
            case "mute_video":
                await self.transport.send(b"%1AVMT 11\r")
            case "unmute_video":
                await self.transport.send(b"%1AVMT 10\r")
            case "mute_audio":
                await self.transport.send(b"%1AVMT 21\r")
            case "unmute_audio":
                await self.transport.send(b"%1AVMT 20\r")
            case _:
                log.warning(f"[{self.device_id}] Unknown command: {command}")

        log.debug(f"[{self.device_id}] Sent command: {command} {params}")

    async def on_data_received(self, data: bytes) -> None:
        """Parse PJLink responses and update state."""
        response = data.decode("ascii", errors="ignore").strip()

        # Skip the initial PJLINK greeting
        if response.startswith("PJLINK"):
            log.debug(f"[{self.device_id}] PJLink greeting: {response}")
            return

        # Skip OK acknowledgements (no state to update)
        if response.endswith("=OK"):
            log.debug(f"[{self.device_id}] ACK: {response}")
            return

        # Skip error responses
        if "=ERR" in response:
            log.warning(f"[{self.device_id}] Error response: {response}")
            return

        if not response.startswith("%1") or "=" not in response:
            return

        # Parse: %1CODE=value
        code_part, value = response[2:].split("=", 1)

        if code_part == "POWR":
            power_state = self.POWER_MAP.get(value, "unknown")
            self.set_state("power", power_state)

        elif code_part == "INPT":
            input_name = self.INPUT_REVERSE.get(value, f"unknown_{value}")
            self.set_state("input", input_name)

        elif code_part == "AVMT":
            # 10/11 = video off/on, 20/21 = audio off/on, 30/31 = both off/on
            if value in ("11", "31"):
                self.set_state("mute_video", True)
            elif value in ("10", "30"):
                self.set_state("mute_video", False)
            if value in ("21", "31"):
                self.set_state("mute_audio", True)
            elif value in ("20", "30"):
                self.set_state("mute_audio", False)

        elif code_part == "LAMP":
            # Format: "12345 0" (hours, lamp_on_flag)
            parts = value.split()
            if parts:
                try:
                    self.set_state("lamp_hours", int(parts[0]))
                except ValueError:
                    pass

        elif code_part == "ERST":
            self.set_state("error_status", value)

        elif code_part == "NAME":
            self.set_state("name", value)

    async def poll(self) -> None:
        """Query all status variables from the projector."""
        if not self.transport or not self.transport.connected:
            return

        try:
            await self.transport.send(b"%1POWR ?\r")
            await asyncio.sleep(0.2)
            await self.transport.send(b"%1INPT ?\r")
            await asyncio.sleep(0.2)
            await self.transport.send(b"%1AVMT ?\r")
            await asyncio.sleep(0.2)
            await self.transport.send(b"%1LAMP ?\r")
        except ConnectionError:
            log.warning(f"[{self.device_id}] Poll failed — not connected")

    def _handle_disconnect(self) -> None:
        """Called by the transport when the connection is lost."""
        self._connected = False
        self.set_state("connected", False)
        log.warning(f"[{self.device_id}] Connection lost")
        # Event emission needs to be scheduled (we're in a sync callback)
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(
                self.events.emit(f"device.disconnected.{self.device_id}")
            )
        except RuntimeError:
            pass
