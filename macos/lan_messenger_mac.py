"""Messagerie locale peer-to-peer pour le reseau prive.

Chaque poste lance ce fichier. L'interface n'est servie qu'a localhost ; les
messages eux-memes transitent en HTTP entre les postes decouverts par broadcast
UDP. Aucun service externe, compte, SMB ou port Internet n'est utilise.
"""

import argparse
import base64
import ipaddress
import json
import mimetypes
import socket
import sys
import threading
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen

# Dans l'executable, les donnees doivent rester a cote du .exe et non dans le
# dossier temporaire ou PyInstaller decompresse le programme a chaque lancement.
ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
DATA_DIR = (Path.home() / "Library" / "Application Support" / "MessagerieLocale") if sys.platform == "darwin" else ROOT / "lan_messenger_data"
MEDIA_DIR = DATA_DIR / "media"
HISTORY_FILE = DATA_DIR / "history.json"
APP_VERSION = "1.1.0"
MAC_VERSION_URL = "https://raw.githubusercontent.com/grosdada/Messagerie-reseau-local/main/macos/VERSION.json"
MAC_DOWNLOAD_URL = "https://github.com/grosdada/Messagerie-reseau-local/raw/refs/heads/main/releases/MessagerieLocale-macOS-arm64.zip"
HTTP_PORT = 8766
DISCOVERY_PORT = 37020
NETWORK = ipaddress.ip_network("192.168.1.0/24")
MAX_IMAGE_BYTES = 6 * 1024 * 1024


def is_allowed(address):
    try:
        ip = ipaddress.ip_address(address)
        return ip.is_loopback or ip in NETWORK
    except ValueError:
        return False


def local_name():
    # Noms lisibles des postes connus. Le nom reste stable après mise à jour de
    # l'exécutable ; les autres machines gardent simplement leur nom Windows.
    known_names = {"DESKTOP-SGPQEIF": "ford"}
    hostname = socket.gethostname().upper()
    return known_names.get(hostname, hostname[:48] or "PC")


class Messenger:
    def __init__(self, name, port):
        self.name = name
        self.port = port
        self.instance_id = str(uuid.uuid4())
        self.peers = {}
        self.lock = threading.Lock()
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        MEDIA_DIR.mkdir(exist_ok=True)
        self.history = self._load_history()

    def _load_history(self):
        try:
            data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (OSError, json.JSONDecodeError):
            return []

    def _save_history(self):
        # Un historique borne garde l'app legere, meme avec des images.
        self.history = self.history[-200:]
        HISTORY_FILE.write_text(json.dumps(self.history, ensure_ascii=False, indent=2), encoding="utf-8")

    def add_message(self, message):
        with self.lock:
            if any(item.get("id") == message["id"] for item in self.history):
                return False
            self.history.append(message)
            self._save_history()
        return True

    def register_peer(self, address, name, port, instance_id):
        if not is_allowed(address) or instance_id == self.instance_id:
            return
        try:
            port = int(port)
            if not 1 <= port <= 65535:
                return
        except (ValueError, TypeError):
            return
        with self.lock:
            self.peers[instance_id] = {
                "id": instance_id, "name": str(name)[:48] or address,
                "address": address, "port": port, "seen": time.time(),
            }

    def active_peers(self):
        cutoff = time.time() - 35
        with self.lock:
            self.peers = {key: peer for key, peer in self.peers.items() if peer["seen"] >= cutoff}
            # Un PC peut annoncer l'ancien et le nouvel executable quelques
            # secondes (voire etre lance deux fois). Une adresse IP ne represente
            # qu'un poste : on n'affiche et on ne contacte donc qu'une fois cette
            # adresse, en gardant son annonce la plus recente.
            by_address = {}
            for peer in self.peers.values():
                current = by_address.get(peer["address"])
                if current is None or peer["seen"] > current["seen"]:
                    by_address[peer["address"]] = peer
            return sorted(by_address.values(), key=lambda peer: peer["name"].lower())

    def announce(self):
        payload = json.dumps({"type": "qbuilder-lan-messenger", "id": self.instance_id,
                              "name": self.name, "port": self.port}).encode("utf-8")
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.sendto(payload, ("255.255.255.255", DISCOVERY_PORT))

    def discovery_loop(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", DISCOVERY_PORT))
        sock.settimeout(1)
        last_announcement = 0
        while True:
            if time.time() - last_announcement > 10:
                try:
                    self.announce()
                except OSError:
                    pass
                last_announcement = time.time()
            try:
                data, remote = sock.recvfrom(4096)
                payload = json.loads(data.decode("utf-8"))
                if payload.get("type") == "qbuilder-lan-messenger":
                    self.register_peer(remote[0], payload.get("name"), payload.get("port"), payload.get("id"))
            except socket.timeout:
                pass
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                pass

    def send_to_peers(self, message):
        delivered, failed = [], []
        body = json.dumps(message, ensure_ascii=False).encode("utf-8")
        for peer in self.active_peers():
            request = Request(f"http://{peer['address']}:{peer['port']}/api/receive", data=body,
                              headers={"Content-Type": "application/json"}, method="POST")
            try:
                with urlopen(request, timeout=4) as response:
                    if response.status != 200:
                        raise OSError(f"HTTP {response.status}")
                delivered.append(peer["name"])
            except OSError:
                failed.append(peer["name"])
        return delivered, failed


def check_update():
    """Only called by the explicit MAJ button; Windows versioning is separate."""
    try:
        with urlopen(MAC_VERSION_URL, timeout=8) as response:
            payload = json.loads(response.read(4096).decode("utf-8"))
        version = payload["version"]
        parts = tuple(int(part) for part in version.split("."))
        if len(parts) != 3 or any(part < 0 for part in parts):
            raise ValueError("Version invalide")
        return {"current": APP_VERSION, "version": version,
                "available": parts > tuple(map(int, APP_VERSION.split(".")))}
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise ValueError(f"Vérification impossible : {exc}") from exc


PAGE = r'''<!doctype html><html lang="fr"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Messagerie locale</title><style>
:root{color-scheme:dark;--bg:#11151c;--panel:#1d2430;--line:#364153;--text:#edf2f7;--muted:#9eabbc;--accent:#6ee7b7}*{box-sizing:border-box}body{margin:0;background:linear-gradient(135deg,#10151c,#172235);color:var(--text);font:15px system-ui,sans-serif}.app{max-width:900px;height:100vh;margin:auto;padding:20px;display:grid;grid-template-rows:auto 1fr auto;gap:14px}header,.composer{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:14px 18px}header{display:flex;align-items:center;justify-content:space-between;gap:12px}#update{height:34px;background:transparent;color:var(--accent);border:1px solid var(--accent)}.message{position:relative}.copy-message{position:absolute;top:8px;right:8px;padding:2px 5px;background:transparent;color:var(--muted);font-size:16px}.meta{padding-right:24px}h1{font-size:20px;margin:0 0 5px}.sub{color:var(--muted);font-size:13px}.online{color:var(--accent)}#messages{overflow:auto;padding:8px 2px;display:flex;flex-direction:column;gap:10px}.message{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:11px 14px;max-width:82%}.mine{align-self:flex-end;border-color:#438b73}.meta{font-size:12px;color:var(--muted);margin-bottom:5px}.message p{white-space:pre-wrap;margin:0}.message img{display:block;max-width:100%;max-height:340px;margin-top:9px;border-radius:8px}.composer{display:grid;grid-template-columns:1fr auto;gap:10px}textarea{min-height:62px;resize:vertical;background:#121923;color:var(--text);border:1px solid var(--line);border-radius:10px;padding:10px;font:inherit}textarea.dragover{border-color:var(--accent);box-shadow:0 0 0 3px #6ee7b733}button{background:var(--accent);color:#09251c;border:0;border-radius:10px;padding:0 18px;font-weight:700;cursor:pointer}#image{grid-column:1/2;color:var(--muted)}#notice{font-size:12px;color:var(--muted);grid-column:1/-1}.empty{color:var(--muted);text-align:center;margin-top:40px}@media(max-width:600px){.app{padding:10px}.message{max-width:94%}.composer{grid-template-columns:1fr}.composer button{height:42px}#image{grid-column:auto}}
</style><body><main class="app"><header><div><h1>Messagerie locale</h1><div class="sub"><span id="identity"></span> · <span class="online" id="peers">Recherche des PC…</span></div></div><button type="button" id="update" title="Vérifier les mises à jour macOS">MAJ</button></header><section id="messages"></section><form class="composer" id="form"><textarea id="text" maxlength="4000" placeholder="Écrire à tous les PC connectés…"></textarea><button>Envoyer à tous</button><input id="image" type="file" accept="image/png,image/jpeg,image/webp,image/gif"><div id="notice">Les messages et images restent sur le réseau local. Images limitées à 6 Mo.</div></form></main><script>
const messages=document.querySelector('#messages'),notice=document.querySelector('#notice');let first=true;
function esc(v){const n=document.createElement('span');n.textContent=v;return n.innerHTML}function render(items){messages.innerHTML=items.length?'': '<div class="empty">Aucun message pour le moment.</div>';for(const m of items){const el=document.createElement('article');el.className='message '+(m.mine?'mine':'');el.innerHTML=`<button type="button" class="copy-message" title="Copier le message" aria-label="Copier le message">⧉</button><div class="meta">${esc(m.sender)} · ${new Date(m.created_at).toLocaleString()}</div>${m.text?`<p>${esc(m.text)}</p>`:''}${m.image?`<img alt="Image partagée" src="/media/${encodeURIComponent(m.image)}">`:''}`;el.querySelector('.copy-message').addEventListener('click',e=>copyMessage(m.text||(m.image?'Image partagée':''),e.currentTarget));messages.append(el)}if(first||messages.scrollHeight-messages.scrollTop-messages.clientHeight<100)messages.scrollTop=messages.scrollHeight;first=false}
async function copyMessage(value,button){try{try{await navigator.clipboard.writeText(value)}catch{const area=document.createElement('textarea');area.value=value;area.style.position='fixed';area.style.opacity='0';document.body.append(area);area.select();try{if(!document.execCommand('copy'))throw Error('Copie impossible')}finally{area.remove()}}button.textContent='✓';setTimeout(()=>button.textContent='⧉',900)}catch(e){notice.textContent='Copie impossible : sélectionne le texte puis utilise ⌘C.'}}
document.querySelector('#update').addEventListener('click',async e=>{const button=e.currentTarget;button.disabled=true;notice.textContent='Vérification de la version macOS sur GitHub…';try{const r=await fetch('/api/update-check'),d=await r.json();if(!r.ok)throw Error(d.error);if(!d.available){notice.textContent=`Version macOS ${d.current} à jour.`;return}if(!confirm(`Version macOS ${d.version} disponible. Télécharger le ZIP ? Il faudra quitter l’app puis remplacer la copie dans Applications.`))return;const result=await fetch('/api/update-download',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}),data=await result.json();if(!result.ok)throw Error(data.error);notice.textContent='Téléchargement ouvert dans le navigateur. Décompresse le ZIP, quitte la messagerie et remplace l’app dans Applications. Ton historique est conservé.'}catch(e){notice.textContent=e.message}finally{button.disabled=false}});
async function refresh(){try{const r=await fetch('/api/state'),d=await r.json();document.querySelector('#identity').textContent=d.name;document.querySelector('#peers').textContent=d.peers.length?`${d.peers.length} PC connecté${d.peers.length>1?'s':''} : ${d.peers.map(p=>p.name).join(', ')}`:'Aucun autre PC détecté';render(d.history)}catch(e){notice.textContent='Connexion au service local impossible.'}}
const form=document.querySelector('#form'),textBox=document.querySelector('#text'),imageInput=document.querySelector('#image');let droppedFile=null;function chooseDroppedImage(file){if(!file||!file.type.startsWith('image/')){notice.textContent='Dépose une image PNG, JPEG, WebP ou GIF.';return}if(file.size>6*1024*1024){notice.textContent='Image trop lourde (maximum 6 Mo).';return}droppedFile=file;notice.textContent=`Image prête à envoyer : ${file.name}`;}['dragenter','dragover'].forEach(type=>textBox.addEventListener(type,e=>{e.preventDefault();textBox.classList.add('dragover')}));['dragleave','drop'].forEach(type=>textBox.addEventListener(type,e=>{e.preventDefault();textBox.classList.remove('dragover')}));textBox.addEventListener('drop',e=>chooseDroppedImage(e.dataTransfer.files[0]));imageInput.addEventListener('change',()=>{droppedFile=null;if(imageInput.files[0])notice.textContent=`Image prête à envoyer : ${imageInput.files[0].name}`});form.addEventListener('submit',async e=>{e.preventDefault();const text=textBox.value.trim(),file=droppedFile||imageInput.files[0];if(!text&&!file)return;if(file&&file.size>6*1024*1024){notice.textContent='Image trop lourde (maximum 6 Mo).';return}let image=null;if(file){image={name:file.name,type:file.type,data:await new Promise((ok,no)=>{const r=new FileReader;r.onload=()=>ok(r.result.split(',')[1]);r.onerror=no;r.readAsDataURL(file)})}}notice.textContent='Envoi en cours…';try{const r=await fetch('/api/send',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text,image})}),d=await r.json();if(!r.ok)throw Error(d.error);textBox.value='';imageInput.value='';droppedFile=null;notice.textContent=d.delivered.length?`Envoyé à ${d.delivered.join(', ')}.${d.failed.length?' Échec : '+d.failed.join(', '):''}`:'Message enregistré ici ; aucun autre PC détecté.';await refresh()}catch(e){notice.textContent=e.message}});refresh();setInterval(refresh,3000);
</script></body></html>'''


class Handler(BaseHTTPRequestHandler):
    messenger = None

    def log_message(self, fmt, *args):
        print("%s - %s" % (self.address_string(), fmt % args))

    def json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status); self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body))); self.send_header("Cache-Control", "no-store")
        self.end_headers(); self.wfile.write(body)

    def local_only(self):
        return ipaddress.ip_address(self.client_address[0]).is_loopback

    def read_body(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > MAX_IMAGE_BYTES * 2:
            raise ValueError("Taille de requête invalide")
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/media/":
            self.send_error(404); return
        if path.startswith("/media/"):
            if not is_allowed(self.client_address[0]):
                self.send_error(403, "Source refusee"); return
            name = Path(unquote(path[7:])).name; file = MEDIA_DIR / name
            if not file.is_file(): self.send_error(404); return
            body = file.read_bytes(); self.send_response(200); self.send_header("Content-Type", mimetypes.guess_type(file)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body); return
        if not self.local_only():
            self.send_error(403, "Interface reservee au poste local"); return
        if path == "/" or path == "/index.html":
            body = PAGE.encode("utf-8"); self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body); return
        if path == "/api/update-check":
            try: self.json(check_update())
            except ValueError as exc: self.json({"error": str(exc)}, 502)
            return
        if path == "/api/state":
            self.json({"name": self.messenger.name, "peers": self.messenger.active_peers(), "history": self.messenger.history}); return
        self.send_error(404)

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/update-download":
            if not self.local_only(): self.json({"error": "Acces refuse"}, 403); return
            # No remote page may trigger browser launches through a form POST.
            if self.headers.get("Content-Type") != "application/json" or self.headers.get("Origin", f"http://127.0.0.1:{self.messenger.port}") != f"http://127.0.0.1:{self.messenger.port}":
                self.json({"error": "Origine refusee"}, 403); return
            if webbrowser.open(MAC_DOWNLOAD_URL): self.json({"ok": True})
            else: self.json({"error": "Impossible d’ouvrir le navigateur"}, 500)
            return
        if path == "/api/receive":
            if not is_allowed(self.client_address[0]): self.json({"error": "Source refusee"}, 403); return
            try: message = build_message(self.read_body(), incoming=True)
            except ValueError as exc: self.json({"error": str(exc)}, 400); return
            self.messenger.add_message(message); self.json({"ok": True}); return
        if path != "/api/send" or not self.local_only(): self.json({"error": "Acces refuse"}, 403); return
        try:
            outbound = self.read_body()
            message = build_message(outbound, sender=self.messenger.name, mine=True)
        except ValueError as exc: self.json({"error": str(exc)}, 400); return
        self.messenger.add_message(message)
        # L'historique local conserve le nom de fichier de l'image ; les pairs,
        # eux, doivent recevoir les octets d'origine afin de la stocker chez eux.
        peer_message = {"id": message["id"], "sender": message["sender"],
                        "text": message["text"], "created_at": message["created_at"],
                        "image": outbound.get("image")}
        delivered, failed = self.messenger.send_to_peers(peer_message)
        self.json({"ok": True, "delivered": delivered, "failed": failed})


def build_message(payload, incoming=False, sender=None, mine=False):
    text = str(payload.get("text") or "").strip()[:4000]
    image = payload.get("image")
    image_name = None
    if image:
        if not isinstance(image, dict) or image.get("type") not in {"image/png", "image/jpeg", "image/webp", "image/gif"}:
            raise ValueError("Format d'image non accepte")
        try: raw = base64.b64decode(image.get("data", ""), validate=True)
        except Exception: raise ValueError("Image invalide")
        if not raw or len(raw) > MAX_IMAGE_BYTES: raise ValueError("Image trop lourde (maximum 6 Mo)")
        ext = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif"}[image["type"]]
        image_name = f"{uuid.uuid4().hex}{ext}"; (MEDIA_DIR / image_name).write_bytes(raw)
    if not text and not image_name: raise ValueError("Message vide")
    return {"id": str(payload.get("id") or uuid.uuid4()), "sender": str(payload.get("sender") or sender or "PC inconnu")[:48],
            "text": text, "image": image_name, "created_at": payload.get("created_at") or time.strftime("%Y-%m-%dT%H:%M:%S"), "mine": mine}


def main():
    parser = argparse.ArgumentParser(description="Messagerie locale Q-Builder")
    parser.add_argument("--name", default=local_name(), help="Nom affiche sur le reseau")
    parser.add_argument("--port", type=int, default=HTTP_PORT, help="Port local du service")
    parser.add_argument("--no-browser", action="store_true", help="N'ouvre pas le navigateur automatiquement")
    args = parser.parse_args()
    # Bind before discovery/browser startup: an occupied port must not create a second peer.
    try:
        server = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    except OSError:
        if sys.platform == "darwin":
            webbrowser.open(f"http://127.0.0.1:{args.port}")
            return
        raise
    messenger = Messenger(args.name, args.port); Handler.messenger = messenger
    threading.Thread(target=messenger.discovery_loop, daemon=True).start()
    print(f"Messagerie locale: http://127.0.0.1:{args.port} (nom: {args.name})")
    print(f"Reseau autorise: {NETWORK}; decouverte UDP {DISCOVERY_PORT}; messages TCP {args.port}")
    if sys.platform == "darwin" and getattr(sys, "frozen", False) and not args.no_browser:
        import webview
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            webview.create_window("Messagerie locale", f"http://127.0.0.1:{args.port}",
                                  width=760, height=640, min_size=(440, 400))
            webview.start()
        finally:
            server.shutdown()
            server.server_close()
        return
    if not args.no_browser:
        threading.Timer(0.7, lambda: webbrowser.open(f"http://127.0.0.1:{args.port}")).start()
    server.serve_forever()


if __name__ == "__main__":
    main()
