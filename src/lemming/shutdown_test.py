"""Tests for graceful shutdown coordination."""

import os
import signal
import unittest

from lemming import shutdown


class TestShutdown(unittest.TestCase):
    def setUp(self):
        self.previous_term = signal.getsignal(signal.SIGTERM)
        self.previous_drain = signal.getsignal(shutdown.DRAIN_SIGNAL)
        shutdown.clear_drain()

    def tearDown(self):
        signal.signal(signal.SIGTERM, self.previous_term)
        signal.signal(shutdown.DRAIN_SIGNAL, self.previous_drain)
        shutdown.clear_drain()

    def test_sigterm_raises_keyboard_interrupt(self):
        """SIGTERM reuses the interrupt path that kills the runner tree."""
        shutdown.install_handlers()

        with self.assertRaises(KeyboardInterrupt):
            os.kill(os.getpid(), signal.SIGTERM)
            # The handler runs between bytecodes; give it something to run on.
            for _ in range(1000):
                pass

    def test_drain_signal_sets_flag_without_raising(self):
        """A drain request must not interrupt the in-flight task."""
        shutdown.install_handlers()
        self.assertFalse(shutdown.drain_requested())

        os.kill(os.getpid(), shutdown.DRAIN_SIGNAL)
        for _ in range(1000):
            pass

        self.assertTrue(shutdown.drain_requested())

    def test_clear_drain_resets_state(self):
        """Clearing lets a subsequent loop start undrained."""
        shutdown.request_drain()
        self.assertTrue(shutdown.drain_requested())

        shutdown.clear_drain()

        self.assertFalse(shutdown.drain_requested())


if __name__ == "__main__":
    unittest.main()
