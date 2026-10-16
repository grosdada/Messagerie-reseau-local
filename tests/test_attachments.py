import base64
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen
import zipfile
from macos import lan_messenger_mac as mac

class AttachmentTests(unittest.TestCase):
    def test_windows_zip_receive_download_and_reload(self):
        archive=io.BytesIO()
        with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_STORED) as z:
            z.writestr('contenu.txt',b'a'*(7*1024*1024))
        raw=archive.getvalue()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with patch.multiple(mac,DATA_DIR=root,MEDIA_DIR=root/'media',HISTORY_FILE=root/'history.json'):
                messenger=mac.Messenger('Mac',8766)
                handler=type('Handler',(mac.Handler,),{'messenger':messenger})
                server=mac.ThreadingHTTPServer(('127.0.0.1',0),handler)
                threading.Thread(target=server.serve_forever,daemon=True).start()
                base=f'http://127.0.0.1:{server.server_port}'
                try:
                    # Windows 1.2.4 attachment payload; no text and no image field.
                    payload={'id':'zip-test','sender':'Windows','text':'','created_at':'2026-10-16T12:00:00','image':None,'attachment':{'name':'archives été.zip','type':'application/x-zip-compressed','data':base64.b64encode(raw).decode()}}
                    req=Request(base+'/api/receive',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
                    with urlopen(req) as response:self.assertEqual(response.status,200)
                    stored=messenger.history[-1]['attachment']
                    with urlopen(base+'/media/'+stored['stored_name']) as response:
                        self.assertEqual(response.read(),raw)
                        self.assertIn("filename*=UTF-8''archives%20",response.headers['Content-Disposition'])
                        self.assertEqual(response.headers['Content-Type'],'application/octet-stream')
                    self.assertEqual(mac.Messenger('Mac',8766).history[-1]['attachment']['name'],'archives été.zip')
                    with urlopen(base+'/api/state') as response:self.assertEqual(json.load(response)['history'][-1]['id'],'zip-test')
                finally:server.shutdown();server.server_close()

    def test_reject_oversized_attachment(self):
        with patch.object(mac.base64,'b64decode',return_value=b'x'*(mac.MAX_FILE_BYTES+1)):
            with self.assertRaisesRegex(ValueError,'20 Mo'):
                mac.build_message({'attachment':{'name':'big.zip','data':'eA=='}})
