# Contributing Drivers

Guide for contributing device drivers to the OpenAVC community library.

## Quick Checklist

1. **Create your driver** using one of these methods:
   - **Driver Builder UI** in the Programmer IDE (visual wizard, exports `.avcdriver`)
   - **Write a `.avcdriver` file** by hand (YAML, no code — for text-based protocols)
   - **Write a Python driver** (subclass `BaseDriver` — for binary/complex protocols)

2. **Test thoroughly** against real hardware or a protocol simulator

3. **Fork this repo** and add your driver to the appropriate category folder:
   - `projectors/` — Projectors
   - `displays/` — Commercial displays
   - `switchers/` — Matrix switchers, presentation switchers, scalers
   - `audio/` — DSPs, mixers, amplifiers, microphones
   - `video/` — Video production software (vMix, OBS, etc.)
   - `cameras/` — PTZ cameras
   - `lighting/` — DMX, Art-Net, sACN
   - `utility/` — Wake-on-LAN, relays, bridges

4. **Update `index.json`** with your driver's metadata entry

5. **Submit a pull request**

## index.json Entry Format

Add an entry to the `drivers` array in `index.json`:

```json
{
    "id": "your_driver_id",
    "name": "Human-Readable Driver Name",
    "file": "category/your_driver_id.avcdriver",
    "format": "avcdriver",
    "category": "switcher",
    "manufacturer": "Manufacturer Name",
    "version": "1.0.0",
    "author": "Your Name",
    "transport": "tcp",
    "verified": false,
    "description": "One-line description of what equipment this controls."
}
```

| Field | Description |
|-------|-------------|
| `id` | Unique identifier, lowercase with underscores |
| `file` | Path relative to repo root |
| `format` | `"avcdriver"` for YAML, `"python"` for .py |
| `category` | One of: projector, display, switcher, audio, video, camera, lighting, utility |
| `transport` | Primary transport: tcp, serial, udp, http |
| `verified` | Set to `false` for new contributions (maintainers verify) |

## Testing Requirements

- Test all commands against real hardware or a simulator
- Verify response parsing returns correct state values
- Test connection and disconnection behavior
- For polled drivers, verify polling works at the configured interval

## Naming Conventions

- Driver IDs: lowercase, underscores (e.g., `extron_sis`, `biamp_tesira`)
- One driver per device family, not per model
- Name should include manufacturer and protocol (e.g., "Extron SIS Protocol")

## License

All contributed drivers must be released under the **MIT License**. By submitting a pull request, you agree to license your driver under MIT.

## Driver Creation Reference

For complete documentation on driver formats, the `.avcdriver` YAML schema, Python driver API, and the Driver Builder UI, see the [Creating Drivers](https://github.com/openavc/openavc/blob/main/docs/creating-drivers.md) guide in the main OpenAVC repo.
