#!/usr/bin/env python3
"""Temporary QR-based file transfer server; files stay on this computer."""

from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, urlsplit
import argparse
import html
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import threading
import time


APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "received"
MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_REQUEST_BYTES = 52 * 1024 * 1024
MAX_STORAGE_BYTES = 1024 * 1024 * 1024
MAX_FILES_PER_UPLOAD = 10
FILE_TTL_SECONDS = 24 * 60 * 60
ID_PATTERN = re.compile(r"^[a-f0-9]{32}$")
CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]+")


def sanitize_filename(filename):
    """Return a display-safe base filename without path components."""
    name = str(filename or "").replace("\\", "/").split("/")[-1]
    name = CONTROL_CHARS.sub("_", name).strip().strip(".")
    name = name[:160]
    return name or "arquivo"


class TransferStore:
    """Persist received files and a small, validated JSON manifest locally."""

    def __init__(self, directory, max_file_bytes=MAX_FILE_BYTES,
                 max_storage_bytes=MAX_STORAGE_BYTES, ttl_seconds=FILE_TTL_SECONDS):
        self.root = Path(directory).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.root / "manifest.json"
        self.max_file_bytes = max_file_bytes
        self.max_storage_bytes = max_storage_bytes
        self.ttl_seconds = ttl_seconds
        self.lock = threading.RLock()
        self.records = self._load_manifest()
        self._expire_files()

    def _path_for(self, file_id):
        if not isinstance(file_id, str) or not ID_PATTERN.fullmatch(file_id):
            return None
        return self.root / f"{file_id}.bin"

    def _load_manifest(self):
        try:
            data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return []
        if not isinstance(data, list):
            return []
        records = []
        for item in data:
            if not isinstance(item, dict):
                continue
            file_id = item.get("id")
            path = self._path_for(file_id)
            if path is None or not path.is_file():
                continue
            try:
                size = path.stat().st_size
                uploaded = float(item.get("uploaded", 0))
            except (OSError, TypeError, ValueError):
                continue
            if size <= 0 or size > self.max_file_bytes or time.time() - uploaded > self.ttl_seconds:
                continue
            records.append({
                "id": file_id,
                "name": sanitize_filename(item.get("name", "arquivo")),
                "size": size,
                "uploaded": uploaded,
                "type": str(item.get("type", "application/octet-stream"))[:120],
            })
        return records

    def _save_manifest(self):
        temp_path = self.manifest_path.with_suffix(".tmp")
        temp_path.write_text(json.dumps(self.records, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp_path, self.manifest_path)

    def _expire_files(self):
        now = time.time()
        remaining = []
        for record in self.records:
            path = self._path_for(record["id"])
            if now - record["uploaded"] > self.ttl_seconds:
                if path:
                    path.unlink(missing_ok=True)
            else:
                remaining.append(record)
        if len(remaining) != len(self.records):
            self.records = remaining
            self._save_manifest()

    def add_file(self, filename, content, content_type="application/octet-stream"):
        if not isinstance(content, bytes) or not content:
            raise ValueError("O arquivo está vazio.")
        if len(content) > self.max_file_bytes:
            raise ValueError("O arquivo excede o limite de tamanho.")
        with self.lock:
            self._expire_files()
            current_size = sum(record["size"] for record in self.records)
            if current_size + len(content) > self.max_storage_bytes:
                raise ValueError("O espaço reservado para transferências está cheio.")
            file_id = secrets.token_hex(16)
            path = self._path_for(file_id)
            path.write_bytes(content)
            record = {
                "id": file_id,
                "name": sanitize_filename(filename),
                "size": len(content),
                "uploaded": time.time(),
                "type": str(content_type or "application/octet-stream")[:120],
            }
            self.records.append(record)
            try:
                self._save_manifest()
            except OSError:
                path.unlink(missing_ok=True)
                self.records.remove(record)
                raise
            return dict(record)

    def list_files(self):
        with self.lock:
            self._expire_files()
            return [dict(record) for record in sorted(self.records, key=lambda item: item["uploaded"], reverse=True)]

    def get_record(self, file_id):
        with self.lock:
            self._expire_files()
            for record in self.records:
                if secrets.compare_digest(record["id"], str(file_id)):
                    return dict(record)
        return None

    def read_file(self, file_id):
        path = self._path_for(file_id)
        record = self.get_record(file_id)
        if path is None or record is None:
            return None
        try:
            return path.read_bytes()
        except OSError:
            return None


def detect_lan_ip():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("1.1.1.1", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def safe_equal(left, right):
    return isinstance(left, str) and isinstance(right, str) and secrets.compare_digest(left, right)


def page_template(title, body, script=""):
    return f"""<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#111715"><title>{html.escape(title)} · Troca Local</title>
<style>
:root{{color-scheme:dark;--bg:#101412;--panel:#19211d;--line:#35433b;--text:#f2f4ef;--muted:#b6c0b8;--green:#a8d6a4;--accent:#d6b675}}
*{{box-sizing:border-box}}body{{margin:0;min-height:100vh;padding:28px 18px;background:radial-gradient(ellipse at 50% 0,#23342b 0,transparent 48%),var(--bg);color:var(--text);font:16px/1.5 system-ui,sans-serif;display:grid;place-items:center}}
main{{width:min(100%,650px);padding:clamp(24px,6vw,48px);background:#151c18f2;border:1px solid var(--line);box-shadow:0 24px 90px #0008}}
.brand{{color:var(--green);font-size:11px;font-weight:800;letter-spacing:3px}}h1{{margin:20px 0 10px;font-size:clamp(32px,8vw,48px);line-height:1.08;letter-spacing:-1.5px}}p{{color:var(--muted);margin:8px 0 18px}}.hint{{font-size:13px;color:#9eaaa1}}
.qr{{display:block;width:min(100%,250px);height:auto;margin:22px auto;padding:10px;background:white;border-radius:8px}}.linkbox{{padding:12px;background:#0c100e;border:1px solid var(--line);overflow-wrap:anywhere;font-size:13px;color:var(--green)}}
input[type=file]{{width:100%;padding:14px;margin:12px 0;background:#0c100e;border:1px dashed #607267;color:var(--text)}}button,.button{{display:inline-flex;align-items:center;justify-content:center;gap:8px;width:100%;padding:14px 18px;border:0;background:var(--green);color:#102016;font-size:15px;font-weight:800;cursor:pointer;text-decoration:none}}button:disabled{{opacity:.55;cursor:wait}}.progress{{height:7px;margin-top:15px;background:#29332d;overflow:hidden}}.progress span{{display:block;width:0;height:100%;background:var(--accent);transition:width .15s}}.status{{min-height:24px;margin:10px 0;color:var(--accent);font-size:13px}}
.files{{list-style:none;margin:22px 0 0;padding:0}}.files li{{display:flex;align-items:center;justify-content:space-between;gap:14px;padding:14px 0;border-top:1px solid var(--line)}}.file-name{{min-width:0;overflow-wrap:anywhere;font-weight:650}}.file-meta{{display:block;color:var(--muted);font-size:12px;font-weight:400}}.download{{flex:0 0 auto;width:auto;padding:9px 12px;font-size:12px}}.empty{{padding:18px 0;border-top:1px solid var(--line);color:var(--muted);font-size:14px}}
.admin-grid{{display:grid;grid-template-columns:1fr 1fr;gap:26px;align-items:center}}.admin-grid .qr{{margin:10px auto}}.admin-link{{margin-top:16px}}.small{{font-size:12px}}footer{{margin-top:24px;padding-top:16px;border-top:1px solid var(--line);color:#93a097;font-size:12px}}
@media(max-width:560px){{.admin-grid{{grid-template-columns:1fr}}.admin-grid .qr{{width:210px}}.files li{{align-items:flex-start}}}}
</style></head><body><main>{body}</main><script>{script}</script></body></html>"""


class TransferHandler(BaseHTTPRequestHandler):
    server_version = "TrocaLocal/1.0"

    def _headers(self, content_type="text/html; charset=utf-8", status=200, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if extra:
            for key, value in extra.items():
                self.send_header(key, value)
        self.end_headers()

    def _send(self, body, content_type="text/html; charset=utf-8", status=200, extra=None):
        encoded = body if isinstance(body, bytes) else body.encode("utf-8")
        self._headers(content_type, status, extra)
        self.wfile.write(encoded)

    def _json(self, data, status=200):
        self._send(json.dumps(data, ensure_ascii=False), "application/json; charset=utf-8", status)

    def _authorized(self, token):
        return safe_equal(token, self.server.session_token)

    def _not_found(self):
        self._send("Página não encontrada ou link inválido.", status=404)

    def do_GET(self):
        request = urlsplit(self.path)
        path = request.path
        if path == "/":
            from urllib.parse import parse_qs
            admin = parse_qs(request.query).get("admin", [""])[0]
            if not safe_equal(admin, self.server.admin_token):
                return self._not_found()
            return self._admin_page()

        if path == "/qr.png":
            from urllib.parse import parse_qs
            admin = parse_qs(request.query).get("admin", [""])[0]
            if not safe_equal(admin, self.server.admin_token):
                return self._not_found()
            return self._qr_image()

        if path == "/api/admin-status":
            from urllib.parse import parse_qs
            admin = parse_qs(request.query).get("admin", [""])[0]
            if not safe_equal(admin, self.server.admin_token):
                return self._json({"error": "Acesso negado."}, 403)
            return self._json({
                "url": f"{self.server.public_url.rstrip('/')}/s/{self.server.session_token}",
                "tunnel": bool(getattr(self.server, "tunnel_url", None)),
            })

        if path.startswith("/s/"):
            token = path.removeprefix("/s/")
            if not self._authorized(token):
                return self._not_found()
            return self._share_page(token)

        if path.startswith("/api/files/"):
            token = path.removeprefix("/api/files/")
            if not self._authorized(token):
                return self._json({"error": "Link inválido."}, 403)
            return self._json({"files": self.server.store.list_files()})

        if path.startswith("/download/"):
            parts = path.split("/")
            if len(parts) != 4 or not self._authorized(parts[2]):
                return self._not_found()
            record = self.server.store.get_record(parts[3])
            content = self.server.store.read_file(parts[3])
            if record is None or content is None:
                return self._not_found()
            filename = quote(record["name"], safe="")
            return self._send(content, "application/octet-stream", 200, {
                "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
                "Content-Length": str(len(content)),
            })

        return self._not_found()

    def do_POST(self):
        request = urlsplit(self.path)
        if not request.path.startswith("/api/upload/"):
            return self._not_found()
        token = request.path.removeprefix("/api/upload/")
        if not self._authorized(token):
            self.close_connection = True
            return self._json({"error": "Link inválido."}, 403)
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0:
            return self._json({"error": "Selecione pelo menos um arquivo."}, 400)
        if length > MAX_REQUEST_BYTES:
            self.close_connection = True
            return self._json({"error": "Envio acima do limite de 50 MB por vez."}, 413)
        content_type = self.headers.get("Content-Type", "")
        if not content_type.lower().startswith("multipart/form-data;"):
            return self._json({"error": "Formato de envio inválido."}, 400)
        raw = self.rfile.read(length)
        message = BytesParser(policy=policy.default).parsebytes(
            f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("ascii", "replace") + raw
        )
        if not message.is_multipart():
            return self._json({"error": "Não foi possível ler os arquivos enviados."}, 400)
        parts = []
        for part in message.iter_parts():
            filename = part.get_filename()
            if filename is None:
                continue
            content = part.get_payload(decode=True) or b""
            if not content:
                continue
            parts.append((filename, content, part.get_content_type()))
            if len(parts) > MAX_FILES_PER_UPLOAD:
                return self._json({"error": "Envie no máximo 10 arquivos por vez."}, 400)
        if not parts:
            return self._json({"error": "Selecione pelo menos um arquivo que não esteja vazio."}, 400)
        try:
            added = [self.server.store.add_file(name, data, mime) for name, data, mime in parts]
        except ValueError as error:
            return self._json({"error": str(error)}, 413)
        except OSError:
            return self._json({"error": "Não foi possível gravar no disco do notebook."}, 500)
        return self._json({"files": added}, 201)

    def _share_url(self):
        base = self.server.public_url.rstrip("/")
        return f"{base}/s/{self.server.session_token}"

    def _qr_image(self):
        qrencode = shutil.which("qrencode")
        if not qrencode:
            return self._send(b"", "image/png", 503)
        try:
            result = subprocess.run(
                [qrencode, "-t", "PNG", "-o", "-", "-s", "8", "-m", "2", self._share_url()],
                check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return self._send(b"", "image/png", 503)
        return self._send(result.stdout, "image/png")

    def _admin_page(self):
        body = f"""<div class="brand">TROCA LOCAL · PAINEL DO NOTEBOOK</div>
<h1>Compartilhe arquivos<br>com um QR code.</h1>
<p>Escaneie com o celular. Os arquivos enviados ficam salvos neste notebook por até 24 horas.</p>
<div class="admin-grid"><div><img class="qr" id="qr" src="/qr.png?admin={self.server.admin_token}" alt="QR code da sessão"><div class="hint" id="network">Preparando o endereço…</div></div>
<div><div class="hint">Link desta sessão</div><div class="linkbox" id="share-link">Carregando endereço…</div>
<p class="hint admin-link">Também pode abrir este link no próprio notebook:</p><a class="button" href="/s/{self.server.session_token}">Abrir sessão neste notebook</a></div></div>
<footer>Limite de 50 MB por envio · Links temporários · Não feche este programa durante a transferência.</footer>"""
        script = f"""const admin={json.dumps(self.server.admin_token)};
async function refresh(){{try{{const r=await fetch('/api/admin-status?admin='+encodeURIComponent(admin),{{cache:'no-store'}});if(!r.ok)return;const d=await r.json();document.querySelector('#share-link').textContent=d.url;document.querySelector('#network').textContent=d.tunnel?'Acesso pela internet ativo.':'Rede local: para outra rede, inicie com --tunnel.';document.querySelector('#qr').src='/qr.png?admin='+encodeURIComponent(admin)+'&v='+Date.now();}}catch(e){{}}}}
refresh();setInterval(refresh,4000);"""
        return self._send(page_template("Painel", body, script))

    def _share_page(self, token):
        body = f"""<div class="brand">TROCA LOCAL · TRANSFERÊNCIA PRIVADA</div>
<h1>Enviar arquivos<br>entre seus aparelhos.</h1>
<p>Escolha arquivos neste aparelho para enviá-los ao notebook e disponibilizá-los aos demais aparelhos desta sessão.</p>
<form id="upload-form"><input id="files-input" type="file" name="files" multiple required><button id="send-button" type="submit">Enviar arquivos</button></form>
<div class="progress" aria-label="Progresso do envio"><span id="progress-bar"></span></div><div class="status" id="status" role="status" aria-live="polite"></div>
<h2>Arquivos disponíveis</h2><ul class="files" id="file-list"><li class="empty">Carregando…</li></ul>
<footer>Os arquivos ficam no notebook e são removidos automaticamente após 24 horas. Baixe apenas arquivos de quem você conhece.</footer>"""
        script = f"""const token={json.dumps(token)};
const list=document.querySelector('#file-list'),statusBox=document.querySelector('#status'),form=document.querySelector('#upload-form'),button=document.querySelector('#send-button'),bar=document.querySelector('#progress-bar');
function sizeText(n){{if(n<1024)return n+' B';if(n<1048576)return (n/1024).toFixed(1)+' KB';return (n/1048576).toFixed(1)+' MB';}}
async function refreshFiles(){{try{{const response=await fetch('/api/files/'+encodeURIComponent(token),{{cache:'no-store'}});if(!response.ok)throw Error();const data=await response.json();list.replaceChildren();if(!data.files.length){{const li=document.createElement('li');li.className='empty';li.textContent='Ainda não há arquivos nesta sessão.';list.append(li);return;}}for(const file of data.files){{const li=document.createElement('li'),info=document.createElement('div'),name=document.createElement('span'),meta=document.createElement('span'),a=document.createElement('a');name.className='file-name';name.textContent=file.name;meta.className='file-meta';meta.textContent=sizeText(file.size)+' · '+new Date(file.uploaded*1000).toLocaleString('pt-BR');name.append(meta);a.className='button download';a.href='/download/'+encodeURIComponent(token)+'/'+encodeURIComponent(file.id);a.textContent='Baixar';info.append(name);li.append(info,a);list.append(li);}}}}catch(e){{statusBox.textContent='Não foi possível atualizar a lista de arquivos.';}}}}
form.addEventListener('submit',event=>{{event.preventDefault();const chosen=document.querySelector('#files-input').files;if(!chosen.length)return;const data=new FormData();for(const file of chosen)data.append('files',file,file.name);button.disabled=true;statusBox.textContent='Enviando…';bar.style.width='0';const xhr=new XMLHttpRequest();xhr.open('POST','/api/upload/'+encodeURIComponent(token));xhr.upload.onprogress=e=>{{if(e.lengthComputable)bar.style.width=Math.round(e.loaded/e.total*100)+'%';}};xhr.onload=()=>{{let result={{}};try{{result=JSON.parse(xhr.responseText);}}catch(e){{}}if(xhr.status>=200&&xhr.status<300){{statusBox.textContent='Envio concluído.';form.reset();}}else{{statusBox.textContent=result.error||'Falha no envio.';}}button.disabled=false;refreshFiles();}};xhr.onerror=()=>{{statusBox.textContent='Falha de conexão durante o envio.';button.disabled=false;}};xhr.send(data);}});
refreshFiles();setInterval(refreshFiles,4000);"""
        return self._send(page_template("Enviar arquivos", body, script))

    def log_message(self, fmt, *args):
        # Request paths contain bearer tokens; do not print them to logs.
        return


def create_server(host, port, store, session_token, admin_token, public_url=None):
    class Server(ThreadingHTTPServer):
        daemon_threads = True
        allow_reuse_address = True

    server = Server((host, port), TransferHandler)
    server.store = store
    server.session_token = session_token
    server.admin_token = admin_token
    server.public_url = public_url or f"http://{detect_lan_ip()}:{server.server_port}"
    return server


def start_tunnel(server):
    cloudflared = shutil.which("cloudflared")
    if not cloudflared:
        raise RuntimeError("cloudflared não está instalado. Instale-o ou inicie sem --tunnel.")
    target = f"http://127.0.0.1:{server.server_port}"
    process = subprocess.Popen(
        [cloudflared, "tunnel", "--url", target, "--no-autoupdate"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
    )
    server.tunnel_process = process

    def read_output():
        for line in process.stdout:
            match = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
            if match:
                server.public_url = match.group(0)
                server.tunnel_url = match.group(0)
                print(f"Túnel público ativo: {match.group(0)}", flush=True)
            elif line.strip():
                print(f"cloudflared: {line.rstrip()}", flush=True)

    threading.Thread(target=read_output, daemon=True).start()
    return process


def main():
    parser = argparse.ArgumentParser(description="Transfira arquivos entre celular e notebook usando QR code.")
    parser.add_argument("--host", default="0.0.0.0", help="Interface de rede (padrão: todas as interfaces)")
    parser.add_argument("--port", type=int, default=8765, help="Porta local (padrão: 8765)")
    parser.add_argument("--data-dir", default=str(DATA_DIR), help="Pasta local onde os arquivos serão guardados")
    parser.add_argument("--public-url", help="Endereço público/base usado pelo QR code")
    parser.add_argument("--tunnel", action="store_true", help="Inicia um Cloudflare Quick Tunnel (requer cloudflared)")
    args = parser.parse_args()

    session_token = secrets.token_urlsafe(24)
    admin_token = secrets.token_urlsafe(24)
    store = TransferStore(args.data_dir)
    server = create_server(args.host, args.port, store, session_token, admin_token, args.public_url)
    server.tunnel_url = None
    admin_url = f"http://127.0.0.1:{server.server_port}/?admin={admin_token}"
    tunnel = None
    if args.tunnel:
        try:
            tunnel = start_tunnel(server)
        except RuntimeError as error:
            server.server_close()
            raise SystemExit(str(error))
    print(f"Painel deste notebook: {admin_url}", flush=True)
    print(f"Endereço na rede local: http://{detect_lan_ip()}:{server.server_port}", flush=True)
    print(f"Arquivos salvos em: {Path(args.data_dir).resolve()}", flush=True)
    print("Pressione Ctrl+C para encerrar. O link da sessão muda ao reiniciar.", flush=True)
    cleanup_stop = threading.Event()

    def cleanup_expired():
        while not cleanup_stop.wait(60):
            try:
                store.list_files()
            except OSError as error:
                print(f"Falha ao limpar arquivos expirados: {error}", flush=True)

    cleanup_thread = threading.Thread(target=cleanup_expired, daemon=True)
    cleanup_thread.start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nEncerrando transferência.", flush=True)
    finally:
        cleanup_stop.set()
        cleanup_thread.join(timeout=1)
        server.shutdown()
        server.server_close()
        if tunnel and tunnel.poll() is None:
            tunnel.terminate()
            try:
                tunnel.wait(timeout=3)
            except subprocess.TimeoutExpired:
                tunnel.kill()


if __name__ == "__main__":
    main()
