import queue
import threading
import unittest

from inputweave.main import InputWeaveApp


class FakeRoot:
    def __init__(self):
        self.quit_calls = 0
        self.destroy_calls = 0

    def quit(self):
        self.quit_calls += 1

    def destroy(self):
        self.destroy_calls += 1


class FakeWorker:
    def __init__(self, alive=False):
        self.alive = alive
        self.join_calls = []

    def is_alive(self):
        return self.alive

    def join(self, timeout):
        self.join_calls.append(timeout)
        self.alive = False


class ShutdownTests(unittest.TestCase):
    def test_close_stops_input_before_destroying_the_window(self):
        app = object.__new__(InputWeaveApp)
        app._closing = False
        app.shutdown = threading.Event()
        app.commands = queue.Queue()
        app.button_capture_handler = object()
        app.button_state_handler = object()
        app.settings_dialog_save = object()
        app.mapping_dialog_save = object()
        app.mapping_dialog = object()
        app.worker = FakeWorker(alive=True)
        app.root = FakeRoot()

        app.close()

        self.assertTrue(app.shutdown.is_set())
        self.assertIsNone(app.button_capture_handler)
        self.assertIsNone(app.button_state_handler)
        self.assertIsNone(app.settings_dialog_save)
        self.assertIsNone(app.mapping_dialog_save)
        self.assertIsNone(app.mapping_dialog)
        self.assertEqual(
            [app.commands.get_nowait() for _ in range(3)],
            [
                ("cancel_button_capture", None),
                ("stop_button_monitor", None),
                ("stop", None),
            ],
        )
        self.assertEqual(app.root.quit_calls, 1)
        self.assertEqual(app.root.destroy_calls, 1)
        self.assertEqual(app.worker.join_calls, [1.0])

    def test_close_is_idempotent(self):
        app = object.__new__(InputWeaveApp)
        app._closing = True
        app.shutdown = threading.Event()
        app.commands = queue.Queue()
        app.root = FakeRoot()

        app.close()

        self.assertFalse(app.shutdown.is_set())
        self.assertTrue(app.commands.empty())
        self.assertEqual(app.root.quit_calls, 0)
        self.assertEqual(app.root.destroy_calls, 0)


if __name__ == "__main__":
    unittest.main()
