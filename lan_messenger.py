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
import os
import socket
import subprocess
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
DATA_DIR = ROOT / "lan_messenger_data"
MEDIA_DIR = DATA_DIR / "media"
HISTORY_FILE = DATA_DIR / "history.json"
HTTP_PORT = 8766
DISCOVERY_PORT = 37020
NETWORK = ipaddress.ip_network("192.168.1.0/24")
MAX_IMAGE_BYTES = 6 * 1024 * 1024
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_REQUEST_BYTES = 30 * 1024 * 1024
APP_VERSION = "1.2.4"
RAW_BASE_URL = "https://raw.githubusercontent.com/grosdada/Messagerie-reseau-local/main"


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


def startup_enabled():
    """Indique si l'application est lancee a l'ouverture de session Windows."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
            winreg.QueryValueEx(key, "MessagerieLocale")
        return True
    except (ImportError, OSError):
        return False


def set_startup(enabled):
    try:
        import winreg
    except ImportError:
        raise ValueError("Cette option est disponible sous Windows uniquement")
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                        r"Software\Microsoft\Windows\CurrentVersion\Run", 0,
                        winreg.KEY_SET_VALUE) as key:
        if enabled:
            if not getattr(sys, "frozen", False):
                raise ValueError("Utilise l'executable MessagerieLocale.exe")
            winreg.SetValueEx(key, "MessagerieLocale", 0, winreg.REG_SZ,
                              f'"{Path(sys.executable).resolve()}"')
        else:
            try:
                winreg.DeleteValue(key, "MessagerieLocale")
            except FileNotFoundError:
                pass


class Messenger:
    def __init__(self, name, port):
        self.name = name
        self.port = port
        self.instance_id = str(uuid.uuid4())
        self.peers = {}
        self.lock = threading.Lock()
        DATA_DIR.mkdir(exist_ok=True)
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


def latest_version():
    try:
        with urlopen(f"{RAW_BASE_URL}/VERSION.json", timeout=8) as response:
            payload = json.loads(response.read().decode("utf-8"))
        version = str(payload.get("version") or "")
        if not version:
            raise ValueError("version absente")
        return version
    except Exception as exc:
        raise ValueError(f"Verification impossible : {exc}")


def schedule_update():
    """Telecharge le binaire puis le remplace apres l'arret de l'application."""
    if not getattr(sys, "frozen", False):
        raise ValueError("La mise a jour automatique est disponible dans l'executable uniquement")
    target = Path(sys.executable).resolve()
    temporary = target.with_suffix(".new.exe")
    try:
        with urlopen(f"{RAW_BASE_URL}/MessagerieLocale.exe", timeout=60) as response:
            binary = response.read()
        if len(binary) < 1_000_000 or not binary.startswith(b"MZ"):
            raise ValueError("fichier de mise a jour invalide")
        temporary.write_bytes(binary)
    except Exception as exc:
        temporary.unlink(missing_ok=True)
        raise ValueError(f"Telechargement impossible : {exc}")
    script = target.with_name("update_messagerie.cmd")
    script.write_text(
        "@echo off\r\n"
        "timeout /t 6 /nobreak > nul\r\n"
        f'move /y "{temporary}" "{target}" > nul\r\n'
        f'start "" "{target}"\r\n'
        'del "%~f0"\r\n', encoding="utf-8")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(["cmd.exe", "/c", str(script)], creationflags=flags)


PAGE = r'''<!doctype html><html lang="fr"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Messagerie locale</title><style>
:root{color-scheme:dark;--bg:#11151c;--panel:#1d2430;--line:#364153;--text:#edf2f7;--muted:#9eabbc;--accent:#6ee7b7}*{box-sizing:border-box}body{margin:0;background:linear-gradient(135deg,#10151c,#172235);color:var(--text);font:15px system-ui,sans-serif}.app{max-width:900px;height:100vh;margin:auto;padding:20px;display:grid;grid-template-rows:auto 1fr auto;gap:14px}header,.composer{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:14px 18px}header{display:flex;align-items:center;justify-content:space-between;gap:12px}h1{font-size:20px;margin:0 0 5px}.sub{color:var(--muted);font-size:13px}.online{color:var(--accent)}#messages{overflow:auto;padding:8px 2px;display:flex;flex-direction:column;gap:10px}.message{position:relative;background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:11px 14px;max-width:82%}.mine{align-self:flex-end;border-color:#438b73}.meta{font-size:12px;color:var(--muted);margin:0 0 5px 27px}.message p{white-space:pre-wrap;margin:0}.message img{display:block;max-width:100%;max-height:340px;margin-top:9px;border-radius:8px}.attachment{display:inline-block;margin-top:9px;color:var(--accent)}.copy-message{position:absolute;top:8px;left:8px;width:20px;height:20px;padding:0;border:0;background:transparent;color:var(--muted);font-size:14px;line-height:1}.copy-message:hover{color:var(--accent)}.composer{display:grid;grid-template-columns:1fr auto;gap:10px}.composer.dragover{border-color:var(--accent);box-shadow:0 0 0 3px #6ee7b733}textarea{min-height:62px;resize:vertical;background:#121923;color:var(--text);border:1px solid var(--line);border-radius:10px;padding:10px;font:inherit}button,#attach{background:var(--accent);color:#09251c;border:0;border-radius:10px;padding:0 18px;font-weight:700;cursor:pointer;display:flex;align-items:center;justify-content:center}#update{background:transparent;border:1px solid var(--accent);color:var(--accent);height:34px}#image{display:none}#attach{grid-column:1/2;height:34px;width:max-content;font-size:13px}#notice{font-size:12px;color:var(--muted);grid-column:1/-1}.empty{color:var(--muted);text-align:center;margin-top:40px}@media(max-width:600px){.app{padding:10px}.message{max-width:94%}.composer{grid-template-columns:1fr}.composer button{height:42px}#attach{grid-column:auto}}
</style><body><main class="app"><header><div><h1>Messagerie locale</h1><div class="sub"><span id="identity"></span> · <span class="online" id="peers">Recherche des PC…</span></div></div><div style="display:flex;gap:8px"><button type="button" id="startup">Démarrer avec Windows</button><button type="button" id="update">MAJ</button></div></header><section id="messages"></section><form class="composer" id="form"><textarea id="text" maxlength="4000" placeholder="Écrire à tous les PC connectés… Dépose aussi un fichier ici."></textarea><button>Envoyer à tous</button><label id="attach" for="image">📎 Joindre un fichier</label><input id="image" type="file"><div id="notice">Texte et fichiers restent sur le réseau local. Pièces jointes limitées à 20 Mo.</div></form></main><script>
const messages=document.querySelector('#messages'),notice=document.querySelector('#notice');let first=true;
function esc(v){const n=document.createElement('span');n.textContent=v;return n.innerHTML}function render(items){messages.innerHTML=items.length?'': '<div class="empty">Aucun message pour le moment.</div>';for(const m of items){const a=m.attachment,legacy=m.image?{stored_name:m.image,name:'Image partagée',type:'image/*'}:null,file=a||legacy,preview=file&&file.type.startsWith('image/'),copy=encodeURIComponent(m.text||file?.name||'');const el=document.createElement('article');el.className='message '+(m.mine?'mine':'');el.innerHTML=`<button class="copy-message" title="Copier" data-copy="${copy}">⧉</button><div class="meta">${esc(m.sender)} · ${new Date(m.created_at).toLocaleString()}</div>${m.text?`<p>${esc(m.text)}</p>`:''}${file?(preview?`<img alt="${esc(file.name)}" src="/media/${encodeURIComponent(file.stored_name)}">`:`<a class="attachment" download="${esc(file.name)}" href="/media/${encodeURIComponent(file.stored_name)}">📎 ${esc(file.name)}</a>`):''}`;messages.append(el)}if(first||messages.scrollHeight-messages.scrollTop-messages.clientHeight<100)messages.scrollTop=messages.scrollHeight;first=false}
messages.addEventListener('click',async e=>{const button=e.target.closest('.copy-message');if(!button)return;try{await navigator.clipboard.writeText(decodeURIComponent(button.dataset.copy));button.textContent='✓';setTimeout(()=>button.textContent='⧉',900)}catch{notice.textContent='Copie impossible.'}});
async function refresh(){try{const r=await fetch('/api/state'),d=await r.json();document.querySelector('#identity').textContent=d.name;document.querySelector('#peers').textContent=d.peers.length?`${d.peers.length} PC connecté${d.peers.length>1?'s':''} : ${d.peers.map(p=>p.name).join(', ')}`:'Aucun autre PC détecté';document.querySelector('#startup').textContent=d.startup?'Démarrage Windows : oui':'Démarrer avec Windows';render(d.history)}catch(e){notice.textContent='Connexion au service local impossible.'}}
const form=document.querySelector('#form'),textBox=document.querySelector('#text'),imageInput=document.querySelector('#image');let droppedFile=null,dragDepth=0;function chooseFile(file){if(!file)return;if(file.size>20*1024*1024){notice.textContent='Fichier trop lourd (maximum 20 Mo).';return}droppedFile=file;notice.textContent=`Fichier prêt à envoyer : ${file.name}`;}form.addEventListener('dragenter',e=>{if(e.dataTransfer.files.length){e.preventDefault();dragDepth++;form.classList.add('dragover')}});form.addEventListener('dragover',e=>{if(e.dataTransfer.files.length)e.preventDefault()});form.addEventListener('dragleave',e=>{dragDepth--;if(dragDepth<=0){dragDepth=0;form.classList.remove('dragover')}});form.addEventListener('drop',e=>{e.preventDefault();dragDepth=0;form.classList.remove('dragover');chooseFile(e.dataTransfer.files[0])});textBox.addEventListener('paste',e=>{const item=[...e.clipboardData.items].find(i=>i.type.startsWith('image/'));if(item){e.preventDefault();chooseFile(item.getAsFile())}});imageInput.addEventListener('change',()=>{droppedFile=null;chooseFile(imageInput.files[0])});form.addEventListener('submit',async e=>{e.preventDefault();const text=textBox.value.trim(),file=droppedFile||imageInput.files[0];if(!text&&!file)return;if(file&&file.size>20*1024*1024){notice.textContent='Fichier trop lourd (maximum 20 Mo).';return}let attachment=null;if(file){attachment={name:file.name,type:file.type||'application/octet-stream',data:await new Promise((ok,no)=>{const r=new FileReader;r.onload=()=>ok(r.result.split(',')[1]);r.onerror=no;r.readAsDataURL(file)})}}notice.textContent='Envoi en cours…';try{const r=await fetch('/api/send',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text,attachment})}),d=await r.json();if(!r.ok)throw Error(d.error);textBox.value='';imageInput.value='';droppedFile=null;notice.textContent=d.delivered.length?`Envoyé à ${d.delivered.join(', ')}.${d.failed.length?' Échec : '+d.failed.join(', '):''}`:'Message enregistré ici ; aucun autre PC détecté.';await refresh()}catch(e){notice.textContent=e.message}});document.querySelector('#startup').addEventListener('click',async()=>{try{const state=await (await fetch('/api/state')).json(),enabled=!state.startup,r=await fetch('/api/startup',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled})}),d=await r.json();if(!r.ok)throw Error(d.error);notice.textContent=enabled?'La messagerie démarrera avec Windows.':'Démarrage automatique désactivé.';await refresh()}catch(e){notice.textContent=e.message}});document.querySelector('#update').addEventListener('click',async()=>{notice.textContent='Vérification de la mise à jour…';try{const r=await fetch('/api/update-check'),d=await r.json();if(!r.ok)throw Error(d.error);if(!d.available){notice.textContent='Application déjà à jour.';return}if(!confirm(`Version ${d.version} disponible. Installer maintenant ?`))return;notice.textContent='Téléchargement de la mise à jour…';const u=await fetch('/api/update',{method:'POST'}),result=await u.json();if(!u.ok)throw Error(result.error);notice.textContent='Mise à jour installée : relancement…'}catch(e){notice.textContent=e.message}});refresh();setInterval(refresh,3000);
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
        if length <= 0 or length > MAX_REQUEST_BYTES:
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
            content_type = mimetypes.guess_type(file)[0] or "application/octet-stream"
            body = file.read_bytes(); self.send_response(200); self.send_header("Content-Type", content_type)
            if not content_type.startswith("image/"):
                self.send_header("Content-Disposition", "attachment")
            self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body); return
        if not self.local_only():
            self.send_error(403, "Interface reservee au poste local"); return
        if path == "/" or path == "/index.html":
            body = PAGE.encode("utf-8"); self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body); return
        if path == "/api/state":
            self.json({"name": self.messenger.name, "peers": self.messenger.active_peers(),
                       "history": self.messenger.history, "startup": startup_enabled()}); return
        if path == "/api/update-check":
            try:
                version = latest_version()
                self.json({"version": version, "current": APP_VERSION, "available": version != APP_VERSION})
            except ValueError as exc:
                self.json({"error": str(exc)}, 502)
            return
        self.send_error(404)

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/receive":
            if not is_allowed(self.client_address[0]): self.json({"error": "Source refusee"}, 403); return
            try: message = build_message(self.read_body(), incoming=True)
            except ValueError as exc: self.json({"error": str(exc)}, 400); return
            self.messenger.add_message(message); self.json({"ok": True}); return
        if path == "/api/update":
            if not self.local_only(): self.json({"error": "Acces refuse"}, 403); return
            try:
                schedule_update()
                self.json({"ok": True})
                threading.Timer(0.5, lambda: os._exit(0)).start()
            except ValueError as exc:
                self.json({"error": str(exc)}, 502)
            return
        if path == "/api/startup":
            if not self.local_only(): self.json({"error": "Acces refuse"}, 403); return
            try:
                set_startup(bool(self.read_body().get("enabled")))
                self.json({"ok": True, "startup": startup_enabled()})
            except ValueError as exc:
                self.json({"error": str(exc)}, 400)
            return
        if path != "/api/send" or not self.local_only(): self.json({"error": "Acces refuse"}, 403); return
        try:
            outbound = self.read_body()
            message = build_message(outbound, sender=self.messenger.name, mine=True)
        except ValueError as exc: self.json({"error": str(exc)}, 400); return
        self.messenger.add_message(message)
        # L'historique local conserve le nom de fichier ; les pairs recoivent les
        # octets d'origine afin de stocker leur propre copie.
        peer_message = {"id": message["id"], "sender": message["sender"],
                        "text": message["text"], "created_at": message["created_at"],
                        "attachment": outbound.get("attachment"),
                        # Les anciennes versions comprennent encore le champ
                        # image : on le conserve pour une image, sans l'utiliser
                        # pour les documents generiques.
                        "image": outbound.get("image") or (
                            outbound.get("attachment") if str((outbound.get("attachment") or {}).get("type", "")).startswith("image/") else None)}
        delivered, failed = self.messenger.send_to_peers(peer_message)
        self.json({"ok": True, "delivered": delivered, "failed": failed})


def build_message(payload, incoming=False, sender=None, mine=False):
    text = str(payload.get("text") or "").strip()[:4000]
    incoming_file = payload.get("attachment") or payload.get("image")
    attachment = None
    if incoming_file:
        if not isinstance(incoming_file, dict): raise ValueError("Fichier invalide")
        name = Path(str(incoming_file.get("name") or "fichier")).name[:160] or "fichier"
        content_type = str(incoming_file.get("type") or "application/octet-stream")[:128]
        try: raw = base64.b64decode(incoming_file.get("data", ""), validate=True)
        except Exception: raise ValueError("Fichier invalide")
        if not raw or len(raw) > MAX_FILE_BYTES: raise ValueError("Fichier trop lourd (maximum 20 Mo)")
        suffix = Path(name).suffix[:12]
        stored_name = f"{uuid.uuid4().hex}{suffix}"; (MEDIA_DIR / stored_name).write_bytes(raw)
        attachment = {"stored_name": stored_name, "name": name, "type": content_type}
    if not text and not attachment: raise ValueError("Message vide")
    return {"id": str(payload.get("id") or uuid.uuid4()), "sender": str(payload.get("sender") or sender or "PC inconnu")[:48],
            "text": text, "attachment": attachment, "image": attachment["stored_name"] if attachment and attachment["type"].startswith("image/") else None,
            "created_at": payload.get("created_at") or time.strftime("%Y-%m-%dT%H:%M:%S"), "mine": mine}


def main():
    parser = argparse.ArgumentParser(description="Messagerie locale Q-Builder")
    parser.add_argument("--name", default=local_name(), help="Nom affiche sur le reseau")
    parser.add_argument("--port", type=int, default=HTTP_PORT, help="Port local du service")
    parser.add_argument("--no-browser", action="store_true", help="N'ouvre pas le navigateur automatiquement")
    args = parser.parse_args()
    messenger = Messenger(args.name, args.port); Handler.messenger = messenger
    threading.Thread(target=messenger.discovery_loop, daemon=True).start()
    print(f"Messagerie locale: http://127.0.0.1:{args.port} (nom: {args.name})")
    print(f"Reseau autorise: {NETWORK}; decouverte UDP {DISCOVERY_PORT}; messages TCP {args.port}")
    if not args.no_browser:
        threading.Timer(0.7, lambda: webbrowser.open(f"http://127.0.0.1:{args.port}")).start()
    ThreadingHTTPServer(("0.0.0.0", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
