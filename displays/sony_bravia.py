"""
OpenAVC Sony Bravia Display Driver.

Controls Sony Bravia TVs and professional displays via the JSON-RPC REST API
over HTTP (port 80). Covers Android TV, Google TV, and Pro Bravia models from
~2013 onwards (W, X, Z, A, XR series).

Authentication uses a Pre-Shared Key (PSK) sent in the X-Auth-PSK HTTP header.
To configure PSK on the TV:
    Settings > Network > Home Network Setup > IP Control
        - Authentication: Normal and Pre-Shared Key
        - Pre-Shared Key: (set your key, e.g., "1234")
        - Simple IP Control: On (also enables Remote Start for power-on)

API overview:
    POST /sony/system    - Power, system info, LED indicator, remote codes
    POST /sony/audio     - Volume, mute
    POST /sony/avContent - Input selection, playing content info
    POST /sony/appControl - Application launch

Each request is a JSON-RPC call:
    {"method": "<name>", "params": [<args>], "id": <n>, "version": "1.0"}

Protocol reference: https://pro-bravia.sony.net/develop/integrate/rest-api/spec/
"""

from __future__ import annotations

from typing import Any, Optional

from server.drivers.base import BaseDriver
from server.transport.http_client import HTTPClientTransport
from server.utils.logger import get_logger

log = get_logger(__name__)

# Map friendly input names to Sony URI format
INPUT_URI_MAP = {
    "hdmi1": "extInput:hdmi?port=1",
    "hdmi2": "extInput:hdmi?port=2",
    "hdmi3": "extInput:hdmi?port=3",
    "hdmi4": "extInput:hdmi?port=4",
    "composite": "extInput:composite?port=1",
    "component": "extInput:component?port=1",
}
# Reverse: URI to friendly name
URI_INPUT_MAP = {v: k for k, v in INPUT_URI_MAP.items()}


class SonyBraviaDriver(BaseDriver):
    """Sony Bravia JSON-RPC REST API driver."""

    DRIVER_INFO = {
        "id": "sony_bravia",
        "name": "Sony Bravia Display",
        "manufacturer": "Sony",
        "category": "display",
        "version": "1.0.0",
        "author": "OpenAVC",
        "description": (
            "Controls Sony Bravia TVs and professional displays via the "
            "JSON-RPC REST API. Supports power, input, volume, mute. "
            "Covers Android TV, Google TV, and Pro Bravia models."
        ),
        "transport": "http",
        "help": {
            "overview": (
                "Controls Sony Bravia displays using the built-in REST API. "
                "Works with Android TV, Google TV, and Pro Bravia series from "
                "~2013 onwards (W, X, Z, A, XR models)."
            ),
            "setup": (
                "1. Connect the TV to the network.\n"
                "2. On the TV, go to Settings > Network > Home Network Setup > IP Control.\n"
                "3. Set Authentication to 'Normal and Pre-Shared Key'.\n"
                "4. Set a Pre-Shared Key (e.g., '1234').\n"
                "5. Set Simple IP Control to 'On' (enables power-on over network).\n"
                "6. Enter the TV's IP address and PSK in the driver config."
            ),
        },
        "discovery": {
            "ports": [80],
            "mac_prefixes": [
                "00:01:4a",
                "00:0a:d9",
                "00:0e:07",
                "00:13:a9",
                "00:1a:80",
                "04:5d:4b",
                "40:b8:9a",
                "54:42:49",
                "a8:93:4a",
                "ac:9b:0a",
                "fc:f1:52",
            ],
        },
        "default_config": {
            "host": "",
            "port": 80,
            "psk": "",
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
                "default": 80,
                "label": "Port",
            },
            "psk": {
                "type": "string",
                "required": True,
                "label": "Pre-Shared Key",
                "description": (
                    "The PSK configured on the TV under Settings > Network > "
                    "Home Network Setup > IP Control > Pre-Shared Key."
                ),
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
                "values": ["off", "on"],
                "label": "Power State",
            },
            "input": {
                "type": "enum",
                "values": list(INPUT_URI_MAP.keys()),
                "label": "Input Source",
            },
            "volume": {
                "type": "integer",
                "label": "Volume",
            },
            "mute": {
                "type": "boolean",
                "label": "Audio Mute",
            },
            "model": {
                "type": "string",
                "label": "Model Name",
            },
        },
        "commands": {
            "power_on": {
                "label": "Power On",
                "params": {},
                "help": "Turn on the display. Requires Simple IP Control enabled on the TV.",
            },
            "power_off": {
                "label": "Power Off",
                "params": {},
                "help": "Turn off the display (standby).",
            },
            "set_volume": {
                "label": "Set Volume",
                "params": {
                    "level": {
                        "type": "integer",
                        "min": 0,
                        "max": 100,
                        "required": True,
                        "help": "Volume level 0-100",
                    },
                },
                "help": "Set the speaker volume.",
            },
            "volume_up": {
                "label": "Volume Up",
                "params": {},
                "help": "Increase volume by 1.",
            },
            "volume_down": {
                "label": "Volume Down",
                "params": {},
                "help": "Decrease volume by 1.",
            },
            "mute_on": {
                "label": "Mute On",
                "params": {},
                "help": "Mute the audio.",
            },
            "mute_off": {
                "label": "Mute Off",
                "params": {},
                "help": "Unmute the audio.",
            },
            "set_input": {
                "label": "Set Input",
                "params": {
                    "input": {
                        "type": "enum",
                        "values": list(INPUT_URI_MAP.keys()),
                        "required": True,
                        "help": "Input source to switch to",
                    },
                },
                "help": "Switch the display input source.",
            },
        },
    }

    _request_id: int = 1

    async def connect(self) -> None:
        """Set up HTTP transport with PSK authentication."""
        host = self.config.get("host", "")
        port = self.config.get("port", 80)
        psk = self.config.get("psk", "")
        base_url = f"http://{host}:{port}"

        self.transport = HTTPClientTransport(
            base_url=base_url,
            auth_type="api_key",
            credentials={"header": "X-Auth-PSK", "key": psk},
            verify_ssl=False,
            timeout=self.config.get("timeout", 10.0),
            name=self.device_id,
        )
        await self.transport.open()

        self._connected = True
        self.set_state("connected", True)
        await self.events.emit(f"device.connected.{self.device_id}")
        log.info(f"[{self.device_id}] Connected to Sony Bravia at {base_url}")

        # Fetch model info once on connect
        await self._fetch_system_info()

        # Start polling
        poll_interval = self.config.get("poll_interval", 15)
        if poll_interval > 0:
            await self.start_polling(poll_interval)

    async def _jsonrpc(
        self,
        service: str,
        method: str,
        params: list | None = None,
        version: str = "1.0",
    ) -> dict | None:
        """
        Send a JSON-RPC request and return the parsed result.

        Args:
            service: API service name (system, audio, avContent, appControl).
            method: RPC method name (e.g., getPowerStatus).
            params: Method parameters (default: empty list).
            version: API version (default: "1.0").

        Returns:
            The "result" field from the response, or None on error.
        """
        if not self.transport or not self.transport.connected:
            return None

        self._request_id += 1
        body = {
            "method": method,
            "params": params or [],
            "id": self._request_id,
            "version": version,
        }

        try:
            response = await self.transport.post(f"/sony/{service}", body=body)
            if not response.ok:
                log.warning(
                    f"[{self.device_id}] {service}/{method} HTTP {response.status_code}"
                )
                return None
            data = response.json_data
            if data and "result" in data:
                return data["result"]
            if data and "error" in data:
                err = data["error"]
                log.warning(f"[{self.device_id}] {service}/{method} error: {err}")
                return None
            return data
        except Exception as e:
            log.warning(f"[{self.device_id}] {service}/{method} failed: {e}")
            return None

    async def _fetch_system_info(self) -> None:
        """Query and cache the TV model name."""
        result = await self._jsonrpc("system", "getSystemInformation")
        if result and isinstance(result, list) and len(result) > 0:
            info = result[0]
            model = info.get("model", "")
            if model:
                self.set_state("model", model)
                log.info(f"[{self.device_id}] Model: {model}")

    async def send_command(
        self, command: str, params: dict[str, Any] | None = None
    ) -> Any:
        """Execute a named command on the Sony Bravia display."""
        params = params or {}

        if not self.transport or not self.transport.connected:
            raise ConnectionError(f"[{self.device_id}] Not connected")

        match command:
            case "power_on":
                await self._jsonrpc(
                    "system", "setPowerStatus", [{"status": True}]
                )
            case "power_off":
                await self._jsonrpc(
                    "system", "setPowerStatus", [{"status": False}]
                )
            case "set_volume":
                level = str(int(params.get("level", 0)))
                await self._jsonrpc(
                    "audio",
                    "setAudioVolume",
                    [{"target": "speaker", "volume": level}],
                )
            case "volume_up":
                await self._jsonrpc(
                    "audio",
                    "setAudioVolume",
                    [{"target": "speaker", "volume": "+1"}],
                )
            case "volume_down":
                await self._jsonrpc(
                    "audio",
                    "setAudioVolume",
                    [{"target": "speaker", "volume": "-1"}],
                )
            case "mute_on":
                await self._jsonrpc(
                    "audio", "setAudioMute", [{"status": True}]
                )
            case "mute_off":
                await self._jsonrpc(
                    "audio", "setAudioMute", [{"status": False}]
                )
            case "set_input":
                input_name = params.get("input", "")
                uri = INPUT_URI_MAP.get(input_name)
                if uri:
                    await self._jsonrpc(
                        "avContent", "setPlayContent", [{"uri": uri}]
                    )
                else:
                    log.warning(
                        f"[{self.device_id}] Unknown input: {input_name}"
                    )
            case _:
                log.warning(f"[{self.device_id}] Unknown command: {command}")

        log.debug(f"[{self.device_id}] Sent command: {command} {params}")

    async def poll(self) -> None:
        """Query the TV for current power, volume, and input status."""
        if not self.transport or not self.transport.connected:
            return

        # Power status
        result = await self._jsonrpc("system", "getPowerStatus")
        if result and isinstance(result, list) and len(result) > 0:
            status = result[0].get("status", "")
            self.set_state("power", "on" if status == "active" else "off")

            # Only poll volume/input if the TV is on
            if status != "active":
                return

        # Volume and mute
        result = await self._jsonrpc("audio", "getVolumeInformation")
        if result and isinstance(result, list):
            # Find the "speaker" target in the result list
            for item_list in result:
                if isinstance(item_list, list):
                    for item in item_list:
                        if isinstance(item, dict) and item.get("target") == "speaker":
                            self.set_state("volume", item.get("volume", 0))
                            self.set_state("mute", bool(item.get("mute", False)))
                            break
                elif isinstance(item_list, dict) and item_list.get("target") == "speaker":
                    self.set_state("volume", item_list.get("volume", 0))
                    self.set_state("mute", bool(item_list.get("mute", False)))
                    break

        # Current input
        result = await self._jsonrpc("avContent", "getPlayingContentInfo")
        if result and isinstance(result, list) and len(result) > 0:
            uri = result[0].get("uri", "")
            input_name = URI_INPUT_MAP.get(uri, uri)
            if input_name:
                self.set_state("input", input_name)
