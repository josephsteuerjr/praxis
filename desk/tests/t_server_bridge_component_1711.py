"""The real native component entry must enable the built-in bridge offline."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

DESK = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(DESK), str(DESK / 'localharness')]
import python_app
from localharness import server_bridge, server_connections


class NativeBridgeComponent(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        override = patch.object(server_connections, 'root', return_value=self.root / 'connections')
        override.start()
        self.addCleanup(override.stop)
        self.app = python_app.Application.__new__(python_app.Application)
        self.app.root = self.app.program = self.root
        self.app.host = None

    def install(self):
        return self.app.invoke('component_install', {'name': 'server-bridge'})

    def test_install_without_selected_agent_creates_the_real_private_marker(self):
        self.assertFalse(server_bridge.component()['installed'])
        result = self.install()
        self.assertEqual(result, {'ok': True, 'state': 'completed', 'restart_required': False})
        self.assertTrue(server_bridge.component()['installed'])
        marker = json.loads((server_connections.root() / 'bridge-component.json').read_bytes())
        self.assertEqual(marker, {'format': server_bridge.FORMAT})

    def test_install_does_not_disconnect_an_existing_session(self):
        host = self.app.host = Mock()
        self.assertTrue(self.install()['ok'])
        self.assertIs(self.app.host, host)
        host.shutdown.assert_not_called()
        host.close.assert_not_called()
        host.invoke.assert_not_called()

    def test_retry_is_successful_and_keeps_the_marker_valid(self):
        self.install()
        first = (server_connections.root() / 'bridge-component.json').read_bytes()
        self.assertTrue(self.install()['ok'])
        self.assertEqual((server_connections.root() / 'bridge-component.json').read_bytes(), first)

    def test_write_failure_reaches_the_ui_without_stopping_the_session(self):
        host = self.app.host = Mock()
        with patch.object(server_bridge, 'write_private', side_effect=OSError('fixture-write-refused')):
            with self.assertRaisesRegex(OSError, 'fixture-write-refused'):
                self.install()
        self.assertIs(self.app.host, host)
        host.shutdown.assert_not_called()
        host.close.assert_not_called()
        self.assertFalse(server_bridge.component()['installed'])


if __name__ == '__main__':
    unittest.main()
