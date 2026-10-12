"""HTTP interoperability with the unchanged Windows source at 87c39a7."""
import base64
import importlib.util
import json
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch, MagicMock
from urllib.request import Request, urlopen
import lan_messenger as mac

spec = importlib.util.spec_from_file_location('windows', Path(__file__).parent / 'fixtures/windows_reference.py')
win = importlib.util.module_from_spec(spec)
spec.loader.exec_module(win)

class ProtocolTests(unittest.TestCase):
    def test_http_both_directions(self):
        with tempfile.TemporaryDirectory() as tmp:
            servers = []
            try:
                for i, module in enumerate((mac, win)):
                    module.DATA_DIR = Path(tmp) / str(i)
                    module.MEDIA_DIR = module.DATA_DIR / 'media'
                    module.HISTORY_FILE = module.DATA_DIR / 'history.json'
                    messenger = module.Messenger('Mac' if i == 0 else 'Windows', 8766)
                    handler = type('TestHandler', (module.Handler,), {'messenger': messenger})
                    server = module.ThreadingHTTPServer(('127.0.0.1', 0), handler)
                    threading.Thread(target=server.serve_forever, daemon=True).start()
                    servers.append((server, messenger, module))
                for source, target in ((servers[0], servers[1]), (servers[1], servers[0])):
                    source[1].register_peer('127.0.0.1', target[1].name, target[0].server_port, target[1].instance_id)
                    for image in (None, {'name': 'test.png', 'type': 'image/png', 'data': base64.b64encode(b'\x89PNG\r\n\x1a\nfixture').decode()}):
                        request = Request(f'http://127.0.0.1:{source[0].server_port}/api/send', data=json.dumps({'text': 'Bonjour été 👋', 'image': image}).encode(), headers={'Content-Type': 'application/json'})
                        with urlopen(request) as response:
                            self.assertEqual(json.load(response)['delivered'], [target[1].name])
                        received = target[1].history[-1]
                        self.assertEqual(received['text'], 'Bonjour été 👋')
                        self.assertEqual(received['sender'], source[1].name)
                        self.assertFalse(received['mine'])
                        if image:
                            self.assertEqual((target[2].MEDIA_DIR / received['image']).read_bytes(), base64.b64decode(image['data']))
            finally:
                for server, _, _ in servers:
                    server.shutdown()
                    server.server_close()

    def test_discovery_wire_format(self):
        for module in (mac, win):
            messenger = module.Messenger.__new__(module.Messenger)
            messenger.name, messenger.port, messenger.instance_id = 'Poste', 8766, 'same-id'
            with patch.object(module.socket, 'socket') as constructor:
                sock = MagicMock()
                constructor.return_value.__enter__.return_value = sock
                messenger.announce()
                payload, destination = sock.sendto.call_args.args
                self.assertEqual(destination, ('255.255.255.255', 37020))
                self.assertEqual(json.loads(payload), {'type': 'qbuilder-lan-messenger', 'id': 'same-id', 'name': 'Poste', 'port': 8766})
                sock.setsockopt.assert_called_with(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

    def test_network_limits(self):
        for address, expected in [('192.168.1.42', True), ('127.0.0.1', True), ('192.168.2.42', False), ('8.8.8.8', False)]:
            self.assertEqual(mac.is_allowed(address), expected)
        self.assertEqual((mac.HTTP_PORT, mac.DISCOVERY_PORT, str(mac.NETWORK)), (8766, 37020, '192.168.1.0/24'))

if __name__ == '__main__':
    unittest.main()
