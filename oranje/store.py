import calendar
import hashlib
import io
import json
import re
import sqlite3
import threading
import uuid
import unicodedata
import zipfile
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from .parser import norm, parse_mail, monday
from .seed import CONTACTS

APP_ID = 1330794062
DOCUMENT_FORMAT = 2
SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS residents(id TEXT PRIMARY KEY, name TEXT NOT NULL, aliases TEXT NOT NULL,
 guardian TEXT NOT NULL, email TEXT NOT NULL, address TEXT NOT NULL, phone TEXT NOT NULL,
 active INTEGER NOT NULL, verified INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS imports(id TEXT PRIMARY KEY, created TEXT NOT NULL, week TEXT NOT NULL,
 source TEXT NOT NULL, days TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS meals(resident_id TEXT NOT NULL REFERENCES residents(id), day TEXT NOT NULL,
 PRIMARY KEY(resident_id, day));
CREATE TABLE IF NOT EXISTS staff(day TEXT PRIMARY KEY, quantity INTEGER NOT NULL CHECK(quantity >= 0));
CREATE TABLE IF NOT EXISTS invoices(id TEXT PRIMARY KEY, resident_id TEXT NOT NULL REFERENCES residents(id),
 month TEXT NOT NULL, version INTEGER NOT NULL, reference TEXT NOT NULL UNIQUE, created TEXT NOT NULL,
 snapshot TEXT NOT NULL, pdf BLOB NOT NULL, sent TEXT, UNIQUE(resident_id, month, version));
CREATE TABLE IF NOT EXISTS payments(id TEXT PRIMARY KEY, resident_id TEXT NOT NULL REFERENCES residents(id),
 month TEXT NOT NULL, day TEXT NOT NULL, cents INTEGER NOT NULL CHECK(cents > 0), note TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, created TEXT NOT NULL, action TEXT NOT NULL, details TEXT NOT NULL);
"""

def stamp():
    return datetime.now().astimezone().isoformat(timespec="seconds")

def uid():
    return uuid.uuid4().hex

def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)

def valid_month(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}", value):
        raise ValueError("Ongeldige maand.")
    date.fromisoformat(value + "-01")
    return value

def valid_day(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError("Ongeldige datum.")
    return value

def integer(value, minimum=0, maximum=1000):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"Verwacht een geheel getal tussen {minimum} en {maximum}.")
    return value

def money(cents):
    return f"€ {cents / 100:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")

def totals(count):
    subtotal = count * 990
    vat = int((Decimal(subtotal) * Decimal("0.06")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return {"count": count, "subtotal": subtotal, "vat": vat, "total": subtotal + vat}

class Conflict(ValueError):
    pass

class Store:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "oranje.sqlite3"
        self.backups = self.directory / "backups"
        self.backups.mkdir(exist_ok=True)
        self.lock = threading.RLock()
        with self.connect() as db:
            db.executescript(SCHEMA)
            db.execute(f"PRAGMA application_id = {APP_ID}")
            db.execute("PRAGMA user_version = 1")
            if not db.execute("SELECT 1 FROM meta WHERE key='revision'").fetchone():
                db.execute("INSERT INTO meta VALUES('revision','0')")
                db.execute("INSERT INTO meta VALUES('settings',?)", (encode({"sender": "", "address": "", "email": "", "iban": "", "payment_note": "Vermeld de afrekenreferentie bij betaling."}),))
                for name, aliases, guardian, email, address, phone in CONTACTS:
                    db.execute("INSERT INTO residents VALUES(?,?,?,?,?,?,?,?,?)", (uid(), name, encode(aliases), guardian, email, address, phone, 1, 0))
                db.execute("INSERT INTO audit(created,action,details) VALUES(?,?,?)", (stamp(), "Inrichting", encode({"note": "Contacten uit foto als concept ingevoerd; controle vereist."})))

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def revision(self, db):
        return int(db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0])

    def backup(self, prefix="dag"):
        destination = self.backups / (f"{prefix}-{date.today().isoformat()}.sqlite3" if prefix == "dag" else f"{prefix}-{datetime.now():%Y%m%d-%H%M%S}-{uid()[:6]}.sqlite3")
        if destination.exists():
            return destination
        temporary = destination.with_suffix(".tmp")
        try:
            with self.connect() as source:
                target = sqlite3.connect(temporary)
                try:
                    source.backup(target)
                finally:
                    target.close()
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
        for old in sorted(self.backups.glob("dag-*.sqlite3"), reverse=True)[30:]:
            old.unlink()
        return destination

    @contextmanager
    def mutation(self, revision):
        with self.lock:
            with self.connect() as db:
                if revision != self.revision(db):
                    raise Conflict("Gegevens zijn intussen gewijzigd. Vernieuw het scherm en controleer opnieuw.")
            self.backup()
            with self.connect() as db:
                yield db
                db.execute("UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='revision'")

    def log(self, db, action, details):
        db.execute("INSERT INTO audit(created,action,details) VALUES(?,?,?)", (stamp(), action, encode(details)))

    def residents(self, db):
        output = []
        payer_names = {r["key"][6:]: r["value"] for r in db.execute("SELECT key,value FROM meta WHERE key LIKE 'payer:%'")}
        for row in db.execute("SELECT * FROM residents ORDER BY name COLLATE NOCASE"):
            person = dict(row)
            person["aliases"] = json.loads(person["aliases"])
            person["payer_name"] = payer_names.get(person["id"], "")
            output.append(person)
        return output

    def settings(self, db):
        return json.loads(db.execute("SELECT value FROM meta WHERE key='settings'").fetchone()[0])

    def save_resident(self, data):
        if "payer_name" in data and (not isinstance(data["payer_name"], str) or len(data["payer_name"].strip()) > 150):
            raise ValueError("De naam op de betalende rekening mag maximaal 150 tekens bevatten.")
        name, guardian, email = (str(data.get(k, "")).strip() for k in ("name", "guardian", "email"))
        if not name or len(name) > 150:
            raise ValueError("Vul een bewonersnaam in van maximaal 150 tekens.")
        verified = bool(data.get("verified"))
        if verified and (not guardian or not re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", email)):
            raise ValueError("Een bevestigde ontvanger vereist een bewindvoerder en geldig e-mailadres.")
        aliases = data.get("aliases", [])
        if not isinstance(aliases, list) or any(not isinstance(x, str) or not x.strip() or len(x) > 150 for x in aliases):
            raise ValueError("Ongeldige naamvarianten.")
        aliases = list(dict.fromkeys(x.strip() for x in aliases if norm(x) != norm(name)))
        rid = data.get("id") or uid()
        with self.mutation(data.get("revision")) as db:
            before = db.execute("SELECT * FROM residents WHERE id=?", (rid,)).fetchone()
            keys = {norm(x) for x in [name, *aliases]}
            for other in self.residents(db):
                if other["id"] != rid and keys & {norm(x) for x in [other["name"], *other["aliases"]]}:
                    raise ValueError("Deze naam of naamvariant hoort al bij " + other["name"] + ".")
            db.execute("INSERT INTO residents VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,aliases=excluded.aliases,guardian=excluded.guardian,email=excluded.email,address=excluded.address,phone=excluded.phone,active=excluded.active,verified=excluded.verified", (rid, name, encode(aliases), guardian, email, str(data.get("address", ""))[:1000], str(data.get("phone", ""))[:200], int(bool(data.get("active", True))), int(verified)))
            payer_before = db.execute("SELECT value FROM meta WHERE key=?", ("payer:" + rid,)).fetchone()
            if "payer_name" in data:
                db.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", ("payer:" + rid, data["payer_name"].strip()))
            self.log(db, "Bewoner opgeslagen", {"id": rid, "before": {**dict(before), "payer_name": payer_before[0] if payer_before else ""} if before else None, "after": {k: v for k, v in data.items() if k != "revision"}})
        return {"id": rid}

    def save_settings(self, data):
        values = {k: str(data.get(k, "")).strip()[:1500] for k in ("sender", "address", "email", "iban", "payment_note")}
        with self.mutation(data.get("revision")) as db:
            self.log(db, "Instellingen gewijzigd", {"before": self.settings(db), "after": values})
            db.execute("UPDATE meta SET value=? WHERE key='settings'", (encode(values),))

    def parse(self, data):
        with self.lock, self.connect() as db:
            return {"blocks": parse_mail(data.get("source"), data.get("week"), self.residents(db)), "revision": self.revision(db)}

    def validate_days(self, db, data):
        start = monday(data.get("week"))
        days = data.get("days")
        if not isinstance(days, list) or not 1 <= len(days) <= 7:
            raise ValueError("Controleer één tot zeven dagen.")
        residents = {r["id"]: r for r in self.residents(db)}
        seen, clean = set(), []
        for row in days:
            day = date.fromisoformat(valid_day(row["date"]))
            if not start <= day <= start + timedelta(days=6) or day in seen:
                raise ValueError("Datums moeten uniek zijn en binnen de gekozen week vallen.")
            seen.add(day)
            ids = row.get("resident_ids")
            if not isinstance(ids, list) or len(set(ids)) != len(ids) or any(x not in residents for x in ids):
                raise ValueError("Ongeldige of dubbele bewoner.")
            if row.get("unresolved"):
                raise ValueError("Los eerst alle onbekende namen op.")
            clean.append({"date": day.isoformat(), "resident_ids": sorted(ids), "staff": integer(row.get("staff"))})
        return clean

    def current_day(self, db, day):
        row = db.execute("SELECT quantity FROM staff WHERE day=?", (day,)).fetchone()
        ids = sorted(r[0] for r in db.execute("SELECT resident_id FROM meals WHERE day=?", (day,)))
        return {"date": day, "resident_ids": ids, "staff": row[0] if row else 0, "registered": row is not None or bool(ids)}

    def overlap(self, db, clean):
        changes = []
        for row in clean:
            old = self.current_day(db, row["date"])
            if old["registered"]:
                changes.append({"date": row["date"], "added": sorted(set(row["resident_ids"]) - set(old["resident_ids"])), "removed": sorted(set(old["resident_ids"]) - set(row["resident_ids"])), "staff_before": old["staff"], "staff_after": row["staff"], "same": old["resident_ids"] == row["resident_ids"] and old["staff"] == row["staff"]})
        return changes

    def preview_import(self, data):
        with self.lock, self.connect() as db:
            clean = self.validate_days(db, data)
            return {"days": clean, "overlap": self.overlap(db, clean), "revision": self.revision(db)}

    def commit_import(self, data):
        if data.get("confirmed") is not True or data.get("overlap_confirmed") is not True:
            raise ValueError("Bevestig de controle en de eventuele vervanging.")
        source = data.get("source")
        if not isinstance(source, str) or not source.strip() or len(source) > 150000:
            raise ValueError("De bronmail ontbreekt of is te lang.")
        with self.mutation(data.get("revision")) as db:
            clean = self.validate_days(db, data)
            before = [self.current_day(db, r["date"]) for r in clean]
            iid = uid()
            db.execute("INSERT INTO imports VALUES(?,?,?,?,?)", (iid, stamp(), data["week"], source, encode(clean)))
            for row in clean:
                db.execute("DELETE FROM meals WHERE day=?", (row["date"],))
                db.executemany("INSERT INTO meals VALUES(?,?)", [(rid, row["date"]) for rid in row["resident_ids"]])
                db.execute("INSERT INTO staff VALUES(?,?) ON CONFLICT(day) DO UPDATE SET quantity=excluded.quantity", (row["date"], row["staff"]))
            self.log(db, "Mail verwerkt", {"import_id": iid, "before": before, "after": clean})
        return {"id": iid}

    def edit_grid(self, data):
        day = valid_day(data.get("day"))
        with self.mutation(data.get("revision")) as db:
            before = self.current_day(db, day)
            if data.get("resident_id"):
                rid = data["resident_id"]
                if not db.execute("SELECT 1 FROM residents WHERE id=?", (rid,)).fetchone():
                    raise ValueError("Bewoner niet gevonden.")
                if data.get("value") not in (0, 1):
                    raise ValueError("Een maaltijd is 0 of 1.")
                if data["value"]:
                    db.execute("INSERT OR IGNORE INTO meals VALUES(?,?)", (rid, day))
                else:
                    db.execute("DELETE FROM meals WHERE resident_id=? AND day=?", (rid, day))
            else:
                quantity = integer(data.get("value"))
                db.execute("INSERT INTO staff VALUES(?,?) ON CONFLICT(day) DO UPDATE SET quantity=excluded.quantity", (day, quantity))
            self.log(db, "Maandraster gewijzigd", {"before": before, "after": self.current_day(db, day)})

    def period_bounds(self, data):
        if data.get("scope") == "week":
            first = monday(data.get("week"))
            last = first + timedelta(days=6)
        elif data.get("scope") == "month":
            first = date.fromisoformat(valid_month(data.get("month")) + "-01")
            last = first.replace(day=calendar.monthrange(first.year, first.month)[1])
        else:
            raise ValueError("Kies een week of een maand om te wissen.")
        return first.isoformat(), last.isoformat()

    def period_summary(self, db, data):
        first, last = self.period_bounds(data)
        meals = [dict(r) for r in db.execute("SELECT * FROM meals WHERE day BETWEEN ? AND ? ORDER BY day,resident_id", (first, last))]
        staff = [dict(r) for r in db.execute("SELECT * FROM staff WHERE day BETWEEN ? AND ? ORDER BY day", (first, last))]
        affected_keys = {(r["resident_id"], r["day"][:7]) for r in meals}
        affected = [{"reference": i["reference"], "resident": i["snapshot"]["resident"]["name"], "sent": i["sent"]}
                    for i in self.invoice_rows(db) if i["latest"] and (i["resident_id"], i["month"]) in affected_keys]
        return {"first": first, "last": last, "scope": data["scope"], "meal_count": len(meals),
                "staff_count": sum(r["quantity"] for r in staff), "day_count": len({r["day"] for r in meals + staff}),
                "affected_invoices": affected, "meals": meals, "staff": staff}

    def preview_clear_period(self, data):
        with self.lock, self.connect() as db:
            summary = self.period_summary(db, data)
            return {k: v for k, v in summary.items() if k not in ("meals", "staff")} | {"revision": self.revision(db)}

    def clear_period(self, data):
        if data.get("confirmed") is not True or data.get("confirmation") != "WISSEN":
            raise ValueError("Bevestig het wissen en typ WISSEN om door te gaan.")
        with self.mutation(data.get("revision")) as db:
            summary = self.period_summary(db, data)
            if not summary["day_count"]:
                raise ValueError("Er zijn geen maaltijdregistraties in deze periode om te wissen.")
            # A full, restorable ZIP must be written successfully before any deletion.
            safety = f"voor-wissen-{datetime.now():%Y%m%d-%H%M%S}-{uid()[:6]}.zip"
            (self.backups / safety).write_bytes(self.export_backup())
            db.execute("DELETE FROM meals WHERE day BETWEEN ? AND ?", (summary["first"], summary["last"]))
            db.execute("DELETE FROM staff WHERE day BETWEEN ? AND ?", (summary["first"], summary["last"]))
            self.log(db, "Week gewist" if summary["scope"] == "week" else "Maand gewist", {**summary, "safety": safety})
        return {k: v for k, v in summary.items() if k not in ("meals", "staff")} | {"safety": safety}

    def snapshot(self, db, resident, month):
        dates = [r[0] for r in db.execute("SELECT day FROM meals WHERE resident_id=? AND substr(day,1,7)=? ORDER BY day", (resident["id"], month))]
        # The bank's account label is matching information, not invoice content.
        invoice_resident = {k: v for k, v in resident.items() if k != "payer_name"}
        return {"resident": invoice_resident, "month": month, "dates": dates, "settings": self.settings(db), "document_format": DOCUMENT_FORMAT, **totals(len(dates))}

    def invoice_rows(self, db):
        rows = []
        people = {r["id"]: r for r in self.residents(db)}
        for row in db.execute("SELECT id,resident_id,month,version,reference,created,snapshot,sent FROM invoices ORDER BY month DESC, resident_id, version DESC"):
            inv = dict(row)
            inv["payer_name"] = people[inv["resident_id"]].get("payer_name", "")
            inv["snapshot"] = json.loads(inv["snapshot"])
            inv["latest"] = not any(x["resident_id"] == inv["resident_id"] and x["month"] == inv["month"] for x in rows)
            current = self.snapshot(db, people[inv["resident_id"]], inv["month"])
            inv["stale"] = encode(current) != encode(inv["snapshot"])
            paid = db.execute("SELECT COALESCE(SUM(cents),0) FROM payments WHERE resident_id=? AND month=?", (inv["resident_id"], inv["month"])).fetchone()[0]
            inv["paid"] = paid
            inv["remaining"] = inv["snapshot"]["total"] - paid
            inv["status"] = "overbetaald" if inv["remaining"] < 0 else "betaald" if inv["remaining"] == 0 else "gedeeltelijk betaald" if paid else "openstaand"
            inv["subject"] = f"Maaltijden {inv['month']} — {inv['snapshot']['resident']['name']} — {inv['reference']}"
            s = inv["snapshot"]
            inv["body"] = f"Beste {s['resident']['guardian']},\n\nHierbij de afrekening voor {s['resident']['name']} voor {inv['month']}.\n\nAantal maaltijden: {s['count']}\nExclusief btw: {money(s['subtotal'])}\nBtw 6%: {money(s['vat'])}\nTe betalen: {money(s['total'])}\n\nRekening: {s['settings']['iban']}\nReferentie: {inv['reference']}\n{s['settings']['payment_note']}\n\nHet maaltijdendetail vindt u in de bijgevoegde afrekening.\n\nMet vriendelijke groeten,\n{s['settings']['sender']}"
            rows.append(inv)
        return rows

    def generate(self, data):
        from .exports import invoice_pdf
        month = valid_month(data.get("month"))
        if data.get("confirmed") is not True:
            raise ValueError("Bevestig dat de maaltijdcorrecties verwerkt zijn.")
        with self.mutation(data.get("revision")) as db:
            settings = self.settings(db)
            if not settings["sender"] or not settings["iban"]:
                raise ValueError("Vul eerst afzender en rekeningnummer in bij Instellingen.")
            candidates = []
            for resident in self.residents(db):
                snapshot = self.snapshot(db, resident, month)
                old = db.execute("SELECT * FROM invoices WHERE resident_id=? AND month=? ORDER BY version DESC LIMIT 1", (resident["id"], month)).fetchone()
                if not snapshot["count"] and not old:
                    continue
                if not resident["verified"]:
                    raise ValueError("Controleer en bevestig eerst de ontvanger van " + resident["name"] + ".")
                candidates.append((resident, snapshot, old))
            ids, replaced_sent = [], []
            for resident, snapshot, old in candidates:
                if old and encode(snapshot) == old["snapshot"]:
                    ids.append(old["id"])
                    continue
                version = old["version"] + 1 if old else 1
                iid = uid()
                readable_name = unicodedata.normalize("NFKD", resident["name"]).encode("ascii", "ignore").decode("ascii")
                readable_name = re.sub(r"[^A-Za-z0-9]+", "-", readable_name).strip("-")[:120].rstrip("-") or "Bewoner"
                base = f"{month[5:7]}-{month[:4]}-{readable_name}"
                reference = f"{base}-V{version}"
                duplicate = 1
                while db.execute("SELECT 1 FROM invoices WHERE reference=?", (reference,)).fetchone():
                    duplicate += 1
                    reference = f"{base}-{duplicate}-V{version}"
                created = stamp()
                pdf = invoice_pdf(snapshot, reference, created, old["reference"] if old else None)
                db.execute("INSERT INTO invoices VALUES(?,?,?,?,?,?,?,?,NULL)", (iid, resident["id"], month, version, reference, created, encode(snapshot), pdf))
                ids.append(iid)
                if old and old["sent"]:
                    replaced_sent.append(old["reference"])
                self.log(db, "Afrekening gemaakt", {"id": iid, "reference": reference, "replaces": old["reference"] if old else None, "total": snapshot["total"]})
            if not candidates:
                raise ValueError("Er zijn geen bewonersmaaltijden voor deze maand.")
        return {"ids": ids, "replaced_sent": replaced_sent}

    def mark_sent(self, data):
        with self.mutation(data.get("revision")) as db:
            inv = next((x for x in self.invoice_rows(db) if x["id"] == data.get("id")), None)
            if not inv or not inv["latest"] or inv["stale"]:
                raise ValueError("Deze afrekening is vervangen of gewijzigd. Maak eerst een actuele versie.")
            value = valid_day(data["day"]) if data.get("day") else None
            db.execute("UPDATE invoices SET sent=? WHERE id=?", (value, inv["id"]))
            self.log(db, "Verzending gewijzigd", {"id": inv["id"], "before": inv["sent"], "after": value})

    def payment(self, data):
        with self.mutation(data.get("revision")) as db:
            pid = data.get("id") or uid()
            old = db.execute("SELECT * FROM payments WHERE id=?", (pid,)).fetchone()
            if data.get("delete"):
                if not old:
                    raise ValueError("Betaling niet gevonden.")
                db.execute("DELETE FROM payments WHERE id=?", (pid,))
                self.log(db, "Betaling verwijderd", dict(old))
                return
            rid, month = data.get("resident_id"), valid_month(data.get("month"))
            if not db.execute("SELECT 1 FROM invoices WHERE resident_id=? AND month=?", (rid, month)).fetchone():
                raise ValueError("Maak eerst een afrekening voor deze bewoner en maand.")
            raw = str(data.get("amount", "")).replace(",", ".")
            if not re.fullmatch(r"\d{1,7}(?:\.\d{1,2})?", raw):
                raise ValueError("Vul een positief bedrag met maximaal twee decimalen in.")
            cents = int(Decimal(raw) * 100)
            if cents <= 0:
                raise ValueError("Een betaling moet groter dan nul zijn.")
            day = valid_day(data.get("day"))
            db.execute("INSERT INTO payments VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET day=excluded.day,cents=excluded.cents,note=excluded.note", (pid, rid, month, day, cents, str(data.get("note", ""))[:1000]))
            self.log(db, "Betaling opgeslagen", {"before": dict(old) if old else None, "after": dict(db.execute("SELECT * FROM payments WHERE id=?", (pid,)).fetchone())})

    def set_date_visibility(self, data):
        day = valid_day(data.get("day"))
        visible = data.get("visible")
        if not isinstance(visible, bool):
            raise ValueError("Kies of de datum zichtbaar moet zijn.")
        with self.mutation(data.get("revision")) as db:
            db.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", ("date:" + day, "1" if visible else "0"))
            self.log(db, "Datumweergave gewijzigd", {"day": day, "visible": visible})

    def state(self, month):
        valid_month(month)
        with self.lock, self.connect() as db:
            residents = self.residents(db)
            meals = [dict(r) for r in db.execute("SELECT * FROM meals WHERE substr(day,1,7)=? ORDER BY day", (month,))]
            staff = [dict(r) for r in db.execute("SELECT * FROM staff WHERE substr(day,1,7)=? ORDER BY day", (month,))]
            first = date.fromisoformat(month + "-01")
            calendar_days = [(first + timedelta(days=i)).isoformat() for i in range(calendar.monthrange(first.year, first.month)[1])]
            registered = {x["day"] for x in meals + staff}
            defaults = {d for d in calendar_days if date.fromisoformat(d).weekday() in (1, 2, 3, 4)} | registered
            overrides = {r["key"][5:]: r["value"] == "1" for r in db.execute("SELECT key,value FROM meta WHERE key LIKE ?", ("date:" + month + "-%",))}
            dates = [d for d in calendar_days if overrides.get(d, d in defaults)]
            export_dates = sorted(set(dates) | registered)
            return {"revision": self.revision(db), "month": month, "residents": residents, "settings": self.settings(db),
                    "meals": meals, "staff": staff, "dates": dates, "calendar_days": calendar_days, "export_dates": export_dates, "invoices": self.invoice_rows(db),
                    "payments": [dict(r) for r in db.execute("SELECT * FROM payments ORDER BY day DESC")],
                    "imports": [dict(r) for r in db.execute("SELECT id,created,week FROM imports ORDER BY created DESC LIMIT 100")],
                    "audit": [dict(r) for r in db.execute("SELECT * FROM audit ORDER BY id DESC LIMIT 250")],
                    "backups": sorted(p.name for p in self.backups.glob("*.sqlite3")),
                    "safety_backups": sorted((p.name for p in self.backups.glob("voor-wissen-*.zip")), reverse=True)}

    def export_backup(self):
        with self.lock, self.connect() as db:
            # Use SQLite's backup API: exports are consistent even during a write.
            temporary = self.directory / ("export-" + uid() + ".tmp")
            copy = sqlite3.connect(temporary)
            try:
                db.backup(copy)
            finally:
                copy.close()
            try:
                payload = temporary.read_bytes()
            finally:
                temporary.unlink(missing_ok=True)
            out = io.BytesIO()
            with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
                z.writestr("oranje.sqlite3", payload)
                z.writestr("manifest.json", encode({"app": "oranje-maaltijden", "schema": 1, "created": stamp(), "sha256": hashlib.sha256(payload).hexdigest()}))
            return out.getvalue()

    def restore(self, payload, revision):
        if len(payload) > 100 * 1024 * 1024:
            raise ValueError("Back-up is te groot (maximaal 100 MB).")
        candidate = self.directory / ("herstel-" + uid() + ".tmp")
        try:
            try:
                with zipfile.ZipFile(io.BytesIO(payload)) as z:
                    if set(z.namelist()) != {"oranje.sqlite3", "manifest.json"} or any(i.file_size > 200 * 1024 * 1024 for i in z.infolist()):
                        raise ValueError("Ongeldige back-upinhoud.")
                    manifest = json.loads(z.read("manifest.json"))
                    database = z.read("oranje.sqlite3")
                if manifest.get("app") != "oranje-maaltijden" or manifest.get("schema") != 1 or hashlib.sha256(database).hexdigest() != manifest.get("sha256"):
                    raise ValueError("De back-upcontrole is mislukt.")
                candidate.write_bytes(database)
                check = sqlite3.connect(candidate)
                try:
                    if check.execute("PRAGMA application_id").fetchone()[0] != APP_ID or check.execute("PRAGMA user_version").fetchone()[0] != 1:
                        raise ValueError("Deze database hoort niet bij deze app-versie.")
                    if check.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or check.execute("PRAGMA foreign_key_check").fetchall():
                        raise ValueError("De back-updatabase is beschadigd.")
                    for table in ("meta", "residents", "imports", "meals", "staff", "invoices", "payments", "audit"):
                        check.execute(f"SELECT * FROM {table} LIMIT 1")
                    int(check.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0])
                    json.loads(check.execute("SELECT value FROM meta WHERE key='settings'").fetchone()[0])
                finally:
                    check.close()
            except (zipfile.BadZipFile, sqlite3.Error, KeyError, TypeError, json.JSONDecodeError) as exc:
                raise ValueError("Dit is geen geldige volledige Oranje-back-up.") from exc
            with self.lock:
                with self.connect() as db:
                    if self.revision(db) != revision:
                        raise Conflict("Gegevens gewijzigd; controleer opnieuw vóór herstel.")
                safety = self.backup("voor-herstel")
                # Every connection is closed before the atomic replacement on Windows.
                staged = sqlite3.connect(candidate)
                try:
                    with staged:
                        staged.execute("UPDATE meta SET value=? WHERE key='revision'", (str(revision + 1),))
                        staged.execute("INSERT INTO audit(created,action,details) VALUES(?,?,?)", (stamp(), "Back-up hersteld", encode({"safety": safety.name})))
                finally:
                    staged.close()
                candidate.replace(self.path)
                return {"safety": safety.name}
        finally:
            candidate.unlink(missing_ok=True)
