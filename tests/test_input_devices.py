import unittest

from inputweave.input_devices import (
    ControllerReader,
    InputDevice,
    button_controls,
    discover_devices,
)


class FakeJoystick:
    def __init__(self, name, guid, axes=6, buttons=14, hats=1, pressed_buttons=()):
        self.name = name
        self.guid = guid
        self.axes = axes
        self.buttons = buttons
        self.hats = hats
        self.pressed_buttons = set(pressed_buttons)
        self.instance_id = 100
        self.closed = False

    def get_name(self):
        return self.name

    def get_guid(self):
        return self.guid

    def get_numaxes(self):
        return self.axes

    def get_numbuttons(self):
        return self.buttons

    def get_numhats(self):
        return self.hats

    def get_instance_id(self):
        return self.instance_id

    def get_button(self, index):
        return index in self.pressed_buttons

    def get_hat(self, _index):
        return (0, 0)

    def quit(self):
        self.closed = True


class FakeJoystickApi:
    def __init__(self, devices):
        self.devices = devices

    def get_count(self):
        return len(self.devices)

    def Joystick(self, index):
        return self.devices[index]


class FakeControllerApi:
    def __init__(self, standardized_indices):
        self.standardized_indices = set(standardized_indices)

    def is_controller(self, index):
        return index in self.standardized_indices

    def Controller(self, _index):
        return FakeController()


class FakeController:
    def get_button(self, _index):
        return False

    def attached(self):
        return True

    def quit(self):
        pass


class FakePygame:
    def __init__(self, devices):
        self.joystick = FakeJoystickApi(devices)

    def __getattr__(self, name):
        if name.startswith("CONTROLLER_BUTTON_"):
            return name
        raise AttributeError(name)


class InputDiscoveryTests(unittest.TestCase):
    def test_discovers_standard_wireless_and_generic_joysticks(self):
        # A DualShock 4 over Bluetooth often reports this generic Windows name.
        wireless_ps4 = FakeJoystick(
            "Wireless Controller", "030000004c050000c405000000010000"
        )
        generic = FakeJoystick(
            "USB HID Gamepad", "03000000000000000000000000000000", axes=4
        )
        devices = discover_devices(
            FakePygame([wireless_ps4, generic]), FakeControllerApi([0])
        )

        self.assertEqual(len(devices), 2)
        self.assertEqual(devices[0].family, "PlayStation")
        self.assertEqual(devices[0].kind, "standard")
        self.assertEqual(devices[0].mapping_key, "PlayStation:standard")
        self.assertEqual(devices[1].kind, "raw")
        self.assertEqual(devices[1].family, "Generischer Controller")
        self.assertTrue(wireless_ps4.closed)
        self.assertTrue(generic.closed)

    def test_standard_playstation_controls_label_touchpad_and_xbox_equivalents(self):
        device = InputDevice(
            index=0,
            name="Wireless Controller",
            family="PlayStation",
            kind="standard",
            guid="",
            axes=6,
            buttons=21,
            hats=0,
        )
        controls = {
            source_id: (label, button)
            for source_id, label, button in button_controls(device)
        }

        self.assertEqual(controls["touchpad"][1], 20)
        self.assertIn("Touchpad", controls["touchpad"][0])
        self.assertNotIn("button_4", controls)
        self.assertNotIn("button_15", controls)
        self.assertIn("✕", controls["south"][0])
        self.assertIn("Xbox A", controls["south"][0])

    def test_raw_playstation_buttons_show_symbolic_suggested_mapping(self):
        device = InputDevice(
            index=0,
            name="Wireless Controller",
            family="PlayStation",
            kind="raw",
            guid="",
            axes=6,
            buttons=14,
            hats=1,
        )
        controls = {
            source_id: label for source_id, label, _button in button_controls(device)
        }

        self.assertIn("raw 0", controls["button_0"])
        self.assertIn("PS Kreuz", controls["button_0"])
        self.assertIn("✕", controls["button_0"])

    def test_standard_playstation_reader_uses_raw_touchpad_button_fallback(self):
        joystick = FakeJoystick(
            "Wireless Controller",
            "030000004c050000c405000000010000",
            buttons=21,
            hats=0,
            pressed_buttons={15},
        )
        device = InputDevice(
            index=0,
            name="Wireless Controller",
            family="PlayStation",
            kind="standard",
            guid=joystick.guid,
            axes=6,
            buttons=21,
            hats=0,
        )
        reader = ControllerReader(
            FakePygame([joystick]), FakeControllerApi([0]), device
        )

        buttons = reader.read_buttons()

        self.assertTrue(buttons["touchpad"])
        self.assertNotIn("button_15", buttons)
        self.assertNotIn("button_4", buttons)


if __name__ == "__main__":
    unittest.main()
