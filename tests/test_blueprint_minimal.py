"""Offline tests for the focused BILRESA light-control blueprint."""
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import re
import unittest

from jinja2 import StrictUndefined
from jinja2.nativetypes import NativeEnvironment
import yaml


class Input(str):
    pass


class Loader(yaml.SafeLoader):
    pass


Loader.add_constructor("!input", lambda loader, node: Input(loader.construct_scalar(node)))
BLUEPRINT = Path(__file__).resolve().parents[1] / "blueprints/automation/OSAZEE/ikea_bilresa_dual_button.yaml"
with BLUEPRINT.open(encoding="utf-8") as blueprint_file:
    CONFIG = yaml.load(blueprint_file, Loader=Loader)


def defaults(inputs):
    result = {}
    for key, definition in inputs.items():
        if "input" in definition:
            result.update(defaults(definition["input"]))
        else:
            result[key] = definition["default"]
    return result


def as_timestamp(value, default=0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def native_event(at, entity, event_type, *, restored=False, old="valid", advancing=True):
    old_state = SimpleNamespace(state=str(1000 + at) if old == "valid" else old, attributes={})
    new_state = SimpleNamespace(
        state=str(1001 + at) if advancing else old_state.state,
        attributes={"event_type": event_type, "restored": restored},
    )
    return at, "state_changed", {"entity_id": entity, "old_state": old_state, "new_state": new_state}


class Runner:
    def __init__(self, lights, pending=(), **overrides):
        self.clock = 0.0
        self.pending = sorted(pending, key=lambda event: event[0])
        self.calls = []
        self.inputs = defaults(CONFIG["blueprint"]["input"])
        self.inputs.update(remote_list=[{"name": "Remote", "button_1": "event.button_1",
                                         "button_2": "event.button_2"}],
                           light_entity=list(lights), latency_compensation_seconds=0)
        self.inputs.update(overrides)
        self.registry = {
            entity: SimpleNamespace(
                state=state,
                attributes={
                    "brightness": brightness,
                    "supported_color_modes": ["brightness", "color_temp"],
                    "color_temp_kelvin": 3500,
                },
            )
            for entity, (state, brightness) in lights.items()
        }
        self.context = {}
        self.env = NativeEnvironment(undefined=StrictUndefined)
        self.env.tests["match"] = lambda value, pattern: re.match(pattern, value) is not None
        self.env.globals.update(
            as_timestamp=as_timestamp,
            now=lambda: datetime.fromtimestamp(1000 + self.clock, timezone.utc),
            has_value=lambda entity: entity in self.registry and self.registry[entity].state not in ("unknown", "unavailable"),
            is_state=lambda entity, state: entity in self.registry and self.registry[entity].state == state,
            is_state_attr=lambda entity, attr, value: entity in self.registry and self.registry[entity].attributes.get(attr) == value,
            state_attr=lambda entity, attr: self.registry[entity].attributes.get(attr) if entity in self.registry else None,
            states=lambda entity: self.registry[entity].state if entity in self.registry else "unknown",
        )

    def render(self, value):
        if isinstance(value, Input):
            return self.inputs[str(value)]
        if isinstance(value, str) and ("{{" in value or "{%" in value):
            return self.env.from_string(value).render(self.context)
        return value

    def variables(self, values):
        for key, value in values.items():
            self.context[key] = self.render(value)

    def conditions(self, condition):
        if isinstance(condition, list):
            return all(self.conditions(item) for item in condition)
        if condition["condition"] == "template":
            value = self.render(condition["value_template"])
            if isinstance(value, str):
                return value.strip().lower() == "true"
            return bool(value)
        raise AssertionError(f"Unsupported condition: {condition}")

    def wait(self, step):
        deadline = self.clock + float(self.render(step["timeout"]["seconds"]))
        while self.pending and self.pending[0][0] <= deadline:
            at, event_type, data = self.pending.pop(0)
            self.clock = at
            for trigger in step["wait_for_trigger"]:
                if event_type == trigger["event_type"] and all(
                    data.get(key) == self.render(value)
                    for key, value in trigger.get("event_data", {}).items()
                ):
                    self.context["wait"] = SimpleNamespace(
                        completed=True,
                        trigger=SimpleNamespace(id=trigger.get("id", "0"), event=SimpleNamespace(data=data)),
                    )
                    return
        self.clock = deadline
        self.context["wait"] = SimpleNamespace(completed=False, trigger=None)

    def sequence(self, steps):
        for step in self.render(steps):
            if "variables" in step:
                self.variables(step["variables"])
            elif "if" in step:
                self.sequence(step.get("then" if self.conditions(step["if"]) else "else", []))
            elif "choose" in step:
                for branch in step["choose"]:
                    if self.conditions(branch["conditions"]):
                        self.sequence(branch["sequence"])
                        break
                else:
                    self.sequence(step.get("default", []))
            elif "repeat" in step:
                repeat = step["repeat"]
                if "for_each" in repeat:
                    previous = self.context.get("repeat")
                    for item in self.render(repeat["for_each"]):
                        self.context["repeat"] = SimpleNamespace(item=item)
                        self.sequence(repeat["sequence"])
                    if previous is None:
                        self.context.pop("repeat", None)
                    else:
                        self.context["repeat"] = previous
                else:
                    for _ in range(100):
                        if not self.conditions(repeat["while"]):
                            break
                        self.sequence(repeat["sequence"])
                    else:
                        raise AssertionError("Unbounded wait")
            elif "wait_for_trigger" in step:
                self.wait(step)
            elif "sequence" in step:
                self.sequence(step["sequence"])
            elif "stop" in step:
                return
            else:
                self.action(step)

    def action(self, step):
        entity = self.render(step["target"]["entity_id"])
        service_data = step.get("data", {})
        data = (self.render(service_data) if isinstance(service_data, str)
            else {key: self.render(value) for key, value in service_data.items()})
        self.calls.append((step["action"], entity, data))
        if step["action"] == "light.turn_off":
            self.registry[entity].state = "off"
        elif step["action"] == "light.turn_on":
            self.registry[entity].state = "on"
            self.registry[entity].attributes.update(data)

    def run(self, event):
        at, event_type, data = event
        self.clock = at
        self.context["trigger"] = SimpleNamespace(event=SimpleNamespace(data=data))
        self.variables(CONFIG["variables"])
        if self.conditions(CONFIG["conditions"]):
            self.sequence(CONFIG["actions"])
        return self.calls


class BlueprintTests(unittest.TestCase):
    def run_event(self, lights, event, pending=(), **options):
        runner = Runner(lights, pending, **options)
        runner.run(event)
        return runner

    def test_blueprint_has_only_requested_inputs_and_event_types(self):
        inputs = defaults(CONFIG["blueprint"]["input"])
        self.assertIn("remote_list", inputs)
        self.assertIn("light_entity", inputs)
        self.assertNotIn("raw_triple_enabled", inputs)
        self.assertNotIn("triple_button_1_action", inputs)
        self.assertNotIn("double_both_action", inputs)
        self.assertNotIn("hold_mode", inputs)
        self.assertEqual(CONFIG["triggers"], [{"trigger": "event", "event_type": "state_changed"}])
        for item in CONFIG["conditions"]:
            if item["condition"] == "template" and "new.attributes.get('event_type') in" in item["value_template"]:
                self.assertIn("long_press", item["value_template"])
                self.assertNotIn("multi_press_3", item["value_template"])

    def test_all_templates_compile_and_input_references_exist(self):
        runner = Runner({})

        def check(value):
            if isinstance(value, Input):
                self.assertIn(str(value), runner.inputs)
            elif isinstance(value, str) and ("{{" in value or "{%" in value):
                runner.env.from_string(value)
            elif isinstance(value, dict):
                for item in value.values():
                    check(item)
            elif isinstance(value, list):
                for item in value:
                    check(item)

        check(CONFIG)

    def test_single_clicks_turn_selected_lights_on_and_off(self):
        lights = {"light.one": ("off", 50), "light.two": ("on", 100)}
        on = self.run_event(lights, native_event(0, "event.button_1", "multi_press_1"))
        self.assertEqual([call[:2] for call in on.calls], [("light.turn_on", "light.one"), ("light.turn_on", "light.two")])
        off = self.run_event(lights, native_event(0, "event.button_2", "multi_press_1"))
        self.assertEqual([call[:2] for call in off.calls], [("light.turn_off", "light.one"), ("light.turn_off", "light.two")])

    def test_double_clicks_step_cooler_and_warmer(self):
        lights = {"light.one": ("on", 100), "light.two": ("on", 140), "light.off": ("off", 80)}
        runner = Runner(lights)
        runner.registry["light.one"].attributes["color_temp_kelvin"] = 3500
        runner.registry["light.two"].attributes["color_temp_kelvin"] = 4800
        runner.registry["light.off"].attributes["color_temp_kelvin"] = 3200
        runner.run(native_event(0, "event.button_1", "multi_press_2"))
        self.assertEqual([call[2]["color_temp_kelvin"] for call in runner.calls], [4000, 5000])
        self.assertTrue(all(call[2]["transition"] == 0.5 for call in runner.calls))
        self.assertEqual(runner.registry["light.off"].state, "off")

        warmer = Runner(lights)
        warmer.registry["light.one"].attributes["color_temp_kelvin"] = 3500
        warmer.registry["light.two"].attributes["color_temp_kelvin"] = 4800
        warmer.run(native_event(0, "event.button_2", "multi_press_2"))
        self.assertEqual([call[2]["color_temp_kelvin"] for call in warmer.calls], [3000, 4300])

    def test_reversed_kelvin_limits_are_normalized(self):
        runner = Runner({"light.one": ("on", 100)}, warm_kelvin=5000, cold_kelvin=2450)
        runner.run(native_event(0, "event.button_1", "multi_press_2"))
        self.assertEqual(runner.calls[0][2]["color_temp_kelvin"], 4000)

    def test_hold_brightens_and_dim_down_stops_at_one(self):
        lights = {"light.one": ("on", 100)}
        release_up = native_event(1, "event.button_1", "long_release")
        up = self.run_event(lights, native_event(0, "event.button_1", "long_press"), [release_up])
        self.assertTrue(any(call[0] == "light.turn_on" and call[2].get("brightness") for call in up.calls))
        release_down = native_event(1, "event.button_2", "long_release")
        down = self.run_event(lights, native_event(0, "event.button_2", "long_press"), [release_down])
        self.assertTrue(any(call[0] == "light.turn_on" and call[2].get("brightness", 255) >= 1 for call in down.calls))

    def test_unavailable_or_restored_events_are_ignored(self):
        for event in (native_event(0, "event.button_1", "multi_press_1", old="unavailable"),
                      native_event(0, "event.button_1", "multi_press_1", restored=True)):
            runner = self.run_event({"light.one": ("off", 30)}, event)
            self.assertEqual(runner.calls, [])


if __name__ == "__main__":
    unittest.main()
