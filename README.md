# IKEA BILRESA Two-Button Light Control

Home Assistant blueprint for controlling selected lights with an IKEA BILRESA dual-button Matter remote.

| Gesture | Button 1 | Button 2 |
| --- | --- | --- |
| Single click | Turn selected lights on | Turn selected lights off |
| Double click | Step cooler | Step warmer |
| Hold | Brighten | Dim |

## Setup

Import the [blueprint](blueprints/automation/OSAZEE/ikea_bilresa_dual_button.yaml), then create an automation from it.

1. Select each remote's Button 1 and Button 2 native event entities. Add more remotes if needed.
2. Select one or more lights to control. Single clicks and holds operate on all selected lights.
3. Set brightness ramp time and release transition to suit the lights.
4. Optionally enable color-temperature dimming during holds and set shared warm/cold limits.

Double-click color-temperature steps are enabled by default: Button 1 steps cooler, Button 2 warmer. Set the shared step size and transition in Light settings. Each selected light uses its own current temperature. Off lights and lights without color-temperature support are skipped.

## Behavior

- Brightening turns selected off lights on at 1%; dimming ignores off lights.
- Unavailable lights are skipped. A failure on one light does not stop commands to other selected lights.
- Double-click temperature steps and dimming share the configured warm/cold limits.
- The automation uses single mode. New gestures are ignored while a dimming run is active.
- The blueprint does not use triple clicks, custom gesture actions, combined-button actions or a companion integration.

## Requirements

- Home Assistant Core 2026.8.0 or newer.
- IKEA BILRESA dual-button Matter remote with two enabled native button event entities.
- Dimmable lights for hold control; color-temperature support is required for temperature changes.

## Updating

Replace the blueprint file at the same path in Home Assistant and reload blueprints. Existing automation inputs are retained; review the new double-click temperature settings after updating.

## Development

Install test dependencies with `python -m pip install -r tests/requirements.txt`, then run `python -B -m unittest discover -s tests -v`.

## License

MIT. See [LICENSE](LICENSE).
