import io
import json
import unittest
from unittest.mock import patch
from macos import lan_messenger_mac as mac

class UpdateTests(unittest.TestCase):
    def check_version(self, version):
        with patch.object(mac, 'urlopen', return_value=io.BytesIO(json.dumps({'version':version}).encode())) as request:
            result = mac.check_update()
            request.assert_called_once_with(mac.MAC_VERSION_URL, timeout=8)
            return result

    def test_mac_versions(self):
        self.assertFalse(self.check_version(mac.APP_VERSION)['available'])
        self.assertFalse(self.check_version('1.0.0')['available'])
        self.assertTrue(self.check_version('1.10.0')['available'])

    def test_invalid_manifest(self):
        for version in (None, 'bad', '1.2', '-1.2.3'):
            with self.subTest(version=version), self.assertRaises(ValueError):
                self.check_version(version)

    def test_network_error_is_actionable(self):
        with patch.object(mac, 'urlopen', side_effect=OSError('hors ligne')):
            with self.assertRaisesRegex(ValueError, 'Vérification impossible'):
                mac.check_update()
