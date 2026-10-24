"""Start met python app.py. Alleen loopback; geen internetdiensten."""
import argparse
import json
import mimetypes
import secrets
import sqlite3
import sys
import threading
import traceback
import webbrowser
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from oranje.store import Store, Conflict, valid_month

ROOT = Path(__file__).resolve().parent

class InstanceLease:
    """One running app per data directory, including when a second port is used."""
    def __init__(self, directory):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        self.file = open(directory / '.instance-lock', 'a+b')
        self.file.seek(0, 2)
        if self.file.tell() == 0:
            self.file.write(b'0')
            self.file.flush()
        self.file.seek(0)
        try:
            if sys.platform == 'win32':
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise ValueError('Deze gegevensmap is al geopend door een andere Oranje-app. Sluit die eerst.')

    def close(self):
        self.file.close()

def make_server(store, port=8765):
    csrf = secrets.token_urlsafe(32)
    class Handler(BaseHTTPRequestHandler):
        def respond(self, payload, status=200, mime="application/json; charset=utf-8", filename=None):
            if not isinstance(payload, bytes):
                payload = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            if filename:
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.end_headers()
            self.wfile.write(payload)

        def valid_host(self):
            return self.headers.get("Host") in {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}

        def do_GET(self):
            try:
                if not self.valid_host():
                    return self.respond({"error": "Alleen lokale toegang toegestaan."}, 403)
                url = urlparse(self.path)
                query = parse_qs(url.query)
                if url.path == "/api/state":
                    state = store.state(query.get("month", [date.today().strftime("%Y-%m")])[0])
                    state["csrf"] = csrf
                    return self.respond(state)
                if url.path == "/api/backup":
                    return self.respond(store.export_backup(), mime="application/zip", filename=f"oranje-backup-{date.today()}.zip")
                if url.path == "/api/safety-backup":
                    name = query.get("name", [""])[0]
                    with store.lock:
                        file = next((p for p in store.backups.glob("voor-wissen-*.zip") if p.name == name), None)
                        if file is None:
                            return self.respond({"error": "Veiligheidskopie niet gevonden."}, 404)
                        return self.respond(file.read_bytes(), mime="application/zip", filename=file.name)
                if url.path == "/api/excel":
                    from oranje.exports import month_xlsx
                    month = valid_month(query.get("month", [""])[0])
                    return self.respond(month_xlsx(store.state(month)), mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", filename=f"maaltijden-{month}.xlsx")
                if url.path == "/api/pdf":
                    with store.lock, store.connect() as db:
                        row = db.execute("SELECT pdf,reference FROM invoices WHERE id=?", (query.get("id", [""])[0],)).fetchone()
                        if not row:
                            return self.respond({"error": "Afrekening niet gevonden."}, 404)
                        return self.respond(bytes(row[0]), mime="application/pdf", filename=row[1] + ".pdf")
                if url.path == "/api/import":
                    with store.lock, store.connect() as db:
                        row = db.execute("SELECT * FROM imports WHERE id=?", (query.get("id", [""])[0],)).fetchone()
                        return self.respond(dict(row) if row else {"error": "Bron niet gevonden."}, 200 if row else 404)
                files = {"/": "index.html", "/app.js": "app.js", "/style.css": "style.css", "/favicon.svg": "favicon.svg", "/favicon.ico": "favicon.svg"}
                if url.path in files:
                    file = ROOT / "static" / files[url.path]
                    return self.respond(file.read_bytes(), mime=(mimetypes.guess_type(str(file))[0] or "text/plain") + "; charset=utf-8")
                self.respond({"error": "Niet gevonden."}, 404)
            except (ValueError, KeyError) as exc:
                self.respond({"error": str(exc)}, 400)
            except Exception:
                traceback.print_exc()
                self.respond({"error": "Er ging iets mis. Controleer het startvenster voor details."}, 500)

        def do_POST(self):
            try:
                if not self.valid_host() or not secrets.compare_digest(self.headers.get("X-Oranje-Token", ""), csrf):
                    return self.respond({"error": "Ongeldige lokale sessie. Vernieuw de pagina."}, 403)
                origin = self.headers.get("Origin")
                if origin and origin not in {f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}"}:
                    return self.respond({"error": "Ongeldige herkomst."}, 403)
                length = int(self.headers.get("Content-Length", 0))
                if not 0 < length <= 100 * 1024 * 1024:
                    raise ValueError("Ongeldige aanvraaggrootte.")
                raw = self.rfile.read(length)
                if self.path == "/api/restore":
                    if self.headers.get("X-Confirm-Restore") != "yes":
                        raise ValueError("Bevestig het vervangen van de huidige gegevens.")
                    result = store.restore(raw, int(self.headers.get("X-Revision", "-1")))
                else:
                    if len(raw) > 2 * 1024 * 1024:
                        raise ValueError("Aanvraag is te groot.")
                    data = json.loads(raw)
                    if not isinstance(data, dict):
                        raise ValueError("Ongeldige aanvraag.")
                    routes = {"/api/resident": store.save_resident, "/api/settings": store.save_settings,
                              "/api/parse": store.parse, "/api/import/preview": store.preview_import,
                              "/api/import/commit": store.commit_import, "/api/grid": store.edit_grid,
                              "/api/generate": store.generate, "/api/sent": store.mark_sent, "/api/payment": store.payment,
                              "/api/dates": store.set_date_visibility,
                              "/api/period/preview": store.preview_clear_period, "/api/period/delete": store.clear_period}
                    if self.path not in routes:
                        return self.respond({"error": "Niet gevonden."}, 404)
                    result = routes[self.path](data)
                self.respond(result or {"ok": True})
            except Conflict as exc:
                self.respond({"error": str(exc)}, 409)
            except (ValueError, KeyError, TypeError, sqlite3.IntegrityError) as exc:
                self.respond({"error": str(exc)}, 400)
            except Exception:
                traceback.print_exc()
                self.respond({"error": "Er ging iets mis. Er zijn geen gedeeltelijke wijzigingen opgeslagen; controleer het startvenster."}, 500)

        def log_message(self, format, *args):
            # Never log bodies containing resident data.
            print(f"{self.address_string()} {format % args}")

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)

def main():
    parser = argparse.ArgumentParser(description="Lokaal maaltijdenbeheer voor Oranje")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    try:
        import reportlab
        import openpyxl
    except ImportError:
        print("Installeer eerst de onderdelen: python -m pip install -r requirements.txt")
        sys.exit(1)
    try:
        lease = InstanceLease(args.data_dir)
    except ValueError as exc:
        print(exc)
        sys.exit(1)
    store = Store(args.data_dir)
    try:
        server = make_server(store, args.port)
    except OSError:
        lease.close()
        print(f"Poort {args.port} is bezet. Mogelijk staat de app al open. Gebruik anders --port 8766.")
        sys.exit(1)
    url = f"http://127.0.0.1:{server.server_port}"
    print(f"Oranje Maaltijdenbeheer\n{url}\nGegevens: {store.directory.resolve()}\nLaat dit venster open tijdens gebruik. Stop met Ctrl+C.", flush=True)
    if not args.no_browser:
        threading.Timer(.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        lease.close()

if __name__ == "__main__":
    main()
