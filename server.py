#!/usr/bin/env python3
"""Cadeaux de Noël : serveur web + API JSON + SQLite, uniquement la bibliothèque standard Python."""
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import sys
import time
import traceback
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

DB_PATH = os.environ.get("CADEAUX_DB", "cadeaux.db")
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
ITERATIONS = 200_000

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE COLLATE NOCASE,
  code_hash TEXT NOT NULL,
  is_admin INTEGER NOT NULL DEFAULT 0,
  changes_seen INTEGER NOT NULL DEFAULT 0 -- dernier événement vu dans « Changements »
);
CREATE TABLE IF NOT EXISTS families (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  budget INTEGER -- en centimes, par personne
);
CREATE TABLE IF NOT EXISTS members (
  family_id INTEGER NOT NULL REFERENCES families(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  PRIMARY KEY (family_id, user_id)
);
CREATE TABLE IF NOT EXISTS gifts (
  id INTEGER PRIMARY KEY,
  owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  price INTEGER, -- en centimes
  link TEXT,
  note TEXT,
  bought INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS givers (
  gift_id INTEGER NOT NULL REFERENCES gifts(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  amount INTEGER, -- participation à la cagnotte en centimes ; NULL = part égale de ce qui reste
  PRIMARY KEY (gift_id, user_id)
);
CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY,
  at INTEGER NOT NULL,
  actor_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, -- à qui appartient la liste
  action TEXT NOT NULL,
  title TEXT NOT NULL,
  detail TEXT
);
"""

# Actions qui trahiraient la surprise : jamais montrées à la personne qui reçoit.
SECRET_ACTIONS = ("give", "join", "amount", "ungive", "bought")


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def db():
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def init_db():
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA journal_mode = WAL")
    con.executescript(SCHEMA)
    # Migrations des bases créées par une version précédente.
    for table, column, decl in (("users", "changes_seen", "INTEGER NOT NULL DEFAULT 0"), ("givers", "amount", "INTEGER")):
        if column not in [r[1] for r in con.execute("PRAGMA table_info(%s)" % table)]:
            con.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, column, decl))
    con.commit()
    con.close()


def log(con, actor, owner, action, title, detail=None):
    con.execute("INSERT INTO events (at, actor_id, owner_id, action, title, detail) VALUES (?, ?, ?, ?, ?, ?)",
                (int(time.time()), actor, owner, action, title, detail))


def fmt_euros(cents):
    if cents is None:
        return "non indiqué"
    return ("%d €" % (cents // 100)) if cents % 100 == 0 else ("%.2f €" % (cents / 100)).replace(".", ",")


# Événements visibles par :me — ceux des listes de sa famille, sans les secrets de sa propre liste.
VISIBLE_EVENTS = """
FROM events e JOIN users a ON a.id = e.actor_id JOIN users o ON o.id = e.owner_id
WHERE CASE WHEN e.owner_id = :me THEN e.action NOT IN %s
      ELSE e.owner_id IN (SELECT m2.user_id FROM members m1 JOIN members m2 ON m1.family_id = m2.family_id
                          WHERE m1.user_id = :me) END
""" % repr(SECRET_ACTIONS)


def rows(con, sql, *args):
    return [dict(r) for r in con.execute(sql, args)]


def hash_code(code):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", code.encode(), salt, ITERATIONS)
    return "pbkdf2$%d$%s$%s" % (ITERATIONS, salt.hex(), digest.hex())


def check_code(code, stored):
    _, iterations, salt, digest = stored.split("$")
    candidate = hashlib.pbkdf2_hmac("sha256", code.encode(), bytes.fromhex(salt), int(iterations))
    return hmac.compare_digest(candidate.hex(), digest)


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


# ---------- validation des données reçues ----------

def text(body, key, maxlen, required=False):
    value = body.get(key)
    if isinstance(value, str):
        value = value.strip()
    if value is None or value == "":
        if required:
            raise ApiError(400, "Merci de remplir tous les champs obligatoires.")
        return None
    if not isinstance(value, str) or len(value) > maxlen:
        raise ApiError(400, "Un des textes est trop long.")
    return value


def money(body, key):
    value = body.get(key)
    if value is None:
        return None
    if type(value) is not int or not 0 <= value <= 10**8:
        raise ApiError(400, "Montant invalide.")
    return value


def link(body):
    value = text(body, "link", 1000)
    if value and not re.match(r"https?://", value, re.I):
        raise ApiError(400, "Le lien doit commencer par http:// ou https://")
    return value


def new_code(body, required=True):
    code = body.get("code")
    if not required and code in (None, ""):
        return None
    if not isinstance(code, str) or not re.fullmatch(r"[0-9]{4,12}", code):
        raise ApiError(400, "Le code secret doit contenir de 4 à 12 chiffres.")
    return hash_code(code)


def id_list(body, key):
    value = body.get(key) or []
    if not isinstance(value, list) or not all(type(v) is int for v in value):
        raise ApiError(400, "Liste invalide.")
    return value


# ---------- règles d'accès ----------

def shares_family(con, a, b):
    return con.execute(
        "SELECT 1 FROM members m1 JOIN members m2 ON m1.family_id = m2.family_id"
        " WHERE m1.user_id = ? AND m2.user_id = ?", (a, b)).fetchone() is not None


def budget_between(con, giver, owner):
    # Si on partage plusieurs familles, on retient le budget le plus petit.
    return con.execute(
        "SELECT MIN(f.budget) FROM families f"
        " JOIN members m1 ON m1.family_id = f.id JOIN members m2 ON m2.family_id = f.id"
        " WHERE m1.user_id = ? AND m2.user_id = ?", (giver, owner)).fetchone()[0]


def gift_for_giver(con, user, gift_id):
    gift = con.execute("SELECT id, owner_id, title FROM gifts WHERE id = ?", (gift_id,)).fetchone()
    if not gift or gift["owner_id"] == user["id"] or not shares_family(con, user["id"], gift["owner_id"]):
        raise ApiError(404, "Ce cadeau n'existe plus.")
    return gift


def set_families(con, user_id, family_ids):
    con.execute("DELETE FROM members WHERE user_id = ?", (user_id,))
    for fid in family_ids:
        con.execute("INSERT OR IGNORE INTO members (family_id, user_id) SELECT id, ? FROM families WHERE id = ?",
                    (user_id, fid))


# ---------- routes ----------

ROUTES = []


def route(method, pattern, auth=True, admin=False):
    def deco(fn):
        ROUTES.append((method, re.compile("^" + pattern + "$"), fn, auth, admin))
        return fn
    return deco


@route("GET", "/api/start", auth=False)
def start(h, con, user, body):
    if user:
        unseen = con.execute("SELECT COUNT(*) " + VISIBLE_EVENTS + " AND e.id > :seen AND e.actor_id <> :me",
                             {"me": user["id"], "seen": user["changes_seen"]}).fetchone()[0]
        return {"me": {"id": user["id"], "name": user["name"], "is_admin": bool(user["is_admin"]), "unseen": unseen}}
    names = rows(con, "SELECT id, name FROM users ORDER BY name")
    return {"me": None, "setup": not names, "names": names}


@route("POST", "/api/setup", auth=False)
def setup(h, con, user, body):
    if con.execute("SELECT 1 FROM users").fetchone():
        raise ApiError(403, "L'application est déjà configurée.")
    name, family, code = text(body, "name", 50, True), text(body, "family", 50, True), new_code(body)
    uid = con.execute("INSERT INTO users (name, code_hash, is_admin) VALUES (?, ?, 1)", (name, code)).lastrowid
    fid = con.execute("INSERT INTO families (name) VALUES (?)", (family,)).lastrowid
    con.execute("INSERT INTO members (family_id, user_id) VALUES (?, ?)", (fid, uid))
    h.start_session(con, uid)


# shortcut: compteur en mémoire, remis à zéro au redémarrage ; suffisant pour un usage familial.
FAILED_LOGINS = {}


@route("POST", "/api/login", auth=False)
def login(h, con, user, body):
    uid = body.get("id")
    row = con.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone() if type(uid) is int else None
    if not row:
        raise ApiError(400, "Choisissez votre prénom.")
    count, locked_until = FAILED_LOGINS.get(uid, (0, 0))
    if locked_until > time.time():
        raise ApiError(429, "Trop d'essais. Patientez 5 minutes puis réessayez.")
    if not check_code(str(body.get("code", "")), row["code_hash"]):
        count += 1
        FAILED_LOGINS[uid] = (0, time.time() + 300) if count >= 5 else (count, 0)
        raise ApiError(401, "Ce code n'est pas le bon. Réessayez.")
    FAILED_LOGINS.pop(uid, None)
    h.start_session(con, uid)


@route("POST", "/api/logout")
def logout(h, con, user, body):
    con.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash(h.session_token()),))
    h.set_cookie("", 0)


# Ma liste : jamais d'information sur qui offre, pour garder la surprise.
@route("GET", "/api/my-gifts")
def my_gifts(h, con, user, body):
    return rows(con, "SELECT id, title, price, link, note FROM gifts WHERE owner_id = ? ORDER BY id", user["id"])


def gift_fields(body):
    return text(body, "title", 120, True), money(body, "price"), link(body), text(body, "note", 500)


@route("POST", "/api/gifts")
def add_gift(h, con, user, body):
    fields = gift_fields(body)
    con.execute("INSERT INTO gifts (owner_id, title, price, link, note) VALUES (?, ?, ?, ?, ?)", (user["id"],) + fields)
    log(con, user["id"], user["id"], "add", fields[0])


def own_gift(con, user, gift_id):
    gift = con.execute("SELECT * FROM gifts WHERE id = ? AND owner_id = ?", (gift_id, user["id"])).fetchone()
    if not gift:
        raise ApiError(404, "Ce cadeau n'existe plus.")
    return gift


@route("PUT", r"/api/gifts/(\d+)")
def edit_gift(h, con, user, body, gift_id):
    old = own_gift(con, user, gift_id)
    title, price, url, note = gift_fields(body)
    con.execute("UPDATE gifts SET title = ?, price = ?, link = ?, note = ? WHERE id = ?", (title, price, url, note, gift_id))
    changes = []
    if old["title"] != title:
        changes.append("nom : « %s » → « %s »" % (old["title"], title))
    if old["price"] != price:
        changes.append("prix : %s → %s" % (fmt_euros(old["price"]), fmt_euros(price)))
    if old["link"] != url:
        changes.append("lien modifié")
    if old["note"] != note:
        changes.append("précisions : %s" % (note or "effacées"))
    if changes:
        log(con, user["id"], user["id"], "edit", title, " · ".join(changes))


@route("DELETE", r"/api/gifts/(\d+)")
def delete_gift(h, con, user, body, gift_id):
    gift = own_gift(con, user, gift_id)
    # Les personnes qui l'offraient l'apprennent par « Changements » (détail masqué au propriétaire).
    givers = [r[0] for r in con.execute("SELECT u.name FROM givers v JOIN users u ON u.id = v.user_id"
                                        " WHERE v.gift_id = ? ORDER BY u.name", (gift_id,))]
    con.execute("DELETE FROM gifts WHERE id = ?", (gift_id,))
    log(con, user["id"], user["id"], "delete", gift["title"], "Il était réservé par " + ", ".join(givers) if givers else None)


@route("GET", "/api/families")
def families(h, con, user, body):
    result = rows(con, "SELECT f.id, f.name, f.budget FROM families f JOIN members m ON m.family_id = f.id"
                       " WHERE m.user_id = ? ORDER BY f.name", user["id"])
    for fam in result:
        fam["people"] = rows(con, """
            SELECT u.id, u.name,
              (SELECT COUNT(*) FROM gifts g WHERE g.owner_id = u.id) AS gifts,
              (SELECT COUNT(*) FROM gifts g WHERE g.owner_id = u.id
                 AND NOT EXISTS (SELECT 1 FROM givers v WHERE v.gift_id = g.id)) AS free
            FROM users u JOIN members m ON m.user_id = u.id
            WHERE m.family_id = ? AND u.id <> ? ORDER BY u.name""", fam["id"], user["id"])
    return result


@route("GET", r"/api/people/(\d+)")
def person(h, con, user, body, person_id):
    person_id = int(person_id)
    if person_id == user["id"] or not shares_family(con, user["id"], person_id):
        raise ApiError(404, "Cette personne n'est pas dans votre famille.")
    gifts = rows(con, "SELECT id, title, price, link, note, bought FROM gifts WHERE owner_id = ? ORDER BY id", person_id)
    for gift in gifts:
        gift["givers"] = givers_of(con, gift["id"])
    name = con.execute("SELECT name FROM users WHERE id = ?", (person_id,)).fetchone()[0]
    return {"id": person_id, "name": name, "budget": budget_between(con, user["id"], person_id), "gifts": gifts}


def givers_of(con, gift_id):
    return rows(con, "SELECT u.id, u.name, v.amount FROM givers v JOIN users u ON u.id = v.user_id"
                     " WHERE v.gift_id = ? ORDER BY u.name", gift_id)


# Sert à « Je l'offre » (sans montant) comme à la cagnotte (avec ou sans montant) ; rappelé, change la participation.
@route("POST", r"/api/gifts/(\d+)/give")
def give(h, con, user, body, gift_id):
    gift = gift_for_giver(con, user, gift_id)
    amount = money(body, "amount")
    if con.execute("SELECT 1 FROM givers WHERE gift_id = ? AND user_id = ?", (gift_id, user["id"])).fetchone():
        con.execute("UPDATE givers SET amount = ? WHERE gift_id = ? AND user_id = ?", (amount, gift_id, user["id"]))
        log(con, user["id"], gift["owner_id"], "amount", gift["title"],
            "Participation : " + (fmt_euros(amount) if amount is not None else "part égale"))
        return
    # Sans "together", on refuse si quelqu'un vient de le réserver entre-temps (évite un cadeau commun involontaire).
    taken = con.execute("SELECT 1 FROM givers WHERE gift_id = ?", (gift_id,)).fetchone()
    if taken and not body.get("together"):
        raise ApiError(409, "Quelqu'un vient de réserver ce cadeau. Vous pouvez participer à la cagnotte.")
    con.execute("INSERT INTO givers (gift_id, user_id, amount) VALUES (?, ?, ?)", (gift_id, user["id"], amount))
    if taken or amount is not None:
        log(con, user["id"], gift["owner_id"], "join", gift["title"],
            "Participation : " + fmt_euros(amount) if amount is not None else None)
    else:
        log(con, user["id"], gift["owner_id"], "give", gift["title"])


@route("DELETE", r"/api/gifts/(\d+)/give")
def ungive(h, con, user, body, gift_id):
    gift = gift_for_giver(con, user, gift_id)
    if con.execute("DELETE FROM givers WHERE gift_id = ? AND user_id = ?", (gift_id, user["id"])).rowcount:
        log(con, user["id"], gift["owner_id"], "ungive", gift["title"])
    con.execute("UPDATE gifts SET bought = 0 WHERE id = ? AND NOT EXISTS (SELECT 1 FROM givers WHERE gift_id = ?)",
                (gift_id, gift_id))


@route("PUT", r"/api/gifts/(\d+)/bought")
def set_bought(h, con, user, body, gift_id):
    gift = gift_for_giver(con, user, gift_id)
    if not con.execute("SELECT 1 FROM givers WHERE gift_id = ? AND user_id = ?", (gift_id, user["id"])).fetchone():
        raise ApiError(403, "Vous n'offrez pas ce cadeau.")
    con.execute("UPDATE gifts SET bought = ? WHERE id = ?", (1 if body.get("bought") else 0, gift_id))
    if body.get("bought"):
        log(con, user["id"], gift["owner_id"], "bought", gift["title"])


@route("GET", "/api/changes")
def changes(h, con, user, body):
    # shortcut: les 150 derniers seulement ; ajouter une pagination si l'historique devient utile au-delà.
    events = [dict(r) for r in con.execute(
        "SELECT e.id, e.at, e.action, e.title, e.detail, e.actor_id, a.name AS actor, e.owner_id, o.name AS owner "
        + VISIBLE_EVENTS + " ORDER BY e.id DESC LIMIT 150", {"me": user["id"]})]
    for e in events:
        if e["owner_id"] == user["id"] and e["action"] == "delete":
            e["detail"] = None  # « réservé par … » trahirait la surprise
    if events:
        con.execute("UPDATE users SET changes_seen = MAX(changes_seen, ?) WHERE id = ?", (events[0]["id"], user["id"]))
    return {"seen": user["changes_seen"], "events": events}


@route("GET", "/api/my-purchases")
def my_purchases(h, con, user, body):
    people = {}
    for gift in rows(con, """
            SELECT g.id, g.title, g.price, g.link, g.note, g.bought, u.id AS owner_id, u.name AS owner_name
            FROM givers v JOIN gifts g ON g.id = v.gift_id JOIN users u ON u.id = g.owner_id
            WHERE v.user_id = ? ORDER BY u.name, g.id""", user["id"]):
        owner = gift.pop("owner_id")
        if owner not in people:
            people[owner] = {"id": owner, "name": gift["owner_name"],
                             "budget": budget_between(con, user["id"], owner), "gifts": []}
        gift["givers"] = givers_of(con, gift["id"])
        people[owner]["gifts"].append(gift)
    return list(people.values())


# ---------- administration ----------

@route("GET", "/api/admin", admin=True)
def admin_data(h, con, user, body):
    users = rows(con, "SELECT id, name, is_admin FROM users ORDER BY name")
    links = con.execute("SELECT user_id, family_id FROM members").fetchall()
    for u in users:
        u["family_ids"] = [fid for uid, fid in links if uid == u["id"]]
    return {"families": rows(con, "SELECT id, name, budget FROM families ORDER BY name"), "users": users}


@route("POST", "/api/admin/families", admin=True)
def add_family(h, con, user, body):
    con.execute("INSERT INTO families (name, budget) VALUES (?, ?)", (text(body, "name", 50, True), money(body, "budget")))


@route("PUT", r"/api/admin/families/(\d+)", admin=True)
def edit_family(h, con, user, body, family_id):
    if not con.execute("UPDATE families SET name = ?, budget = ? WHERE id = ?",
                       (text(body, "name", 50, True), money(body, "budget"), family_id)).rowcount:
        raise ApiError(404, "Cette famille n'existe plus.")


@route("DELETE", r"/api/admin/families/(\d+)", admin=True)
def delete_family(h, con, user, body, family_id):
    con.execute("DELETE FROM families WHERE id = ?", (family_id,))


@route("POST", "/api/admin/users", admin=True)
def add_user(h, con, user, body):
    uid = con.execute("INSERT INTO users (name, code_hash, is_admin) VALUES (?, ?, ?)",
                      (text(body, "name", 50, True), new_code(body), 1 if body.get("is_admin") else 0)).lastrowid
    set_families(con, uid, id_list(body, "family_ids"))


@route("PUT", r"/api/admin/users/(\d+)", admin=True)
def edit_user(h, con, user, body, user_id):
    user_id = int(user_id)
    is_admin = 1 if body.get("is_admin") or user_id == user["id"] else 0  # on ne peut pas se retirer ses propres droits
    code = new_code(body, required=False)
    if not con.execute("UPDATE users SET name = ?, is_admin = ? WHERE id = ?",
                       (text(body, "name", 50, True), is_admin, user_id)).rowcount:
        raise ApiError(404, "Ce membre n'existe plus.")
    if code:
        con.execute("UPDATE users SET code_hash = ? WHERE id = ?", (code, user_id))
        if user_id != user["id"]:
            con.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    set_families(con, user_id, id_list(body, "family_ids"))


@route("DELETE", r"/api/admin/users/(\d+)", admin=True)
def delete_user(h, con, user, body, user_id):
    if int(user_id) == user["id"]:
        raise ApiError(400, "Vous ne pouvez pas supprimer votre propre compte.")
    con.execute("DELETE FROM users WHERE id = ?", (user_id,))


# ---------- serveur HTTP ----------

TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".webmanifest": "application/manifest+json",
         ".png": "image/png", ".svg": "image/svg+xml"}
STATIC = {}


def load_static():
    # Liste blanche chargée au démarrage : aucun chemin demandé n'atteint le disque.
    for name in os.listdir(STATIC_DIR):
        ext = os.path.splitext(name)[1]
        if ext in TYPES and not name.startswith("."):
            with open(os.path.join(STATIC_DIR, name), "rb") as f:
                STATIC["/" + name] = (f.read(), TYPES[ext])
    STATIC["/"] = STATIC["/index.html"]


class Handler(BaseHTTPRequestHandler):
    server_version = "Cadeaux"

    def do_GET(self):
        self.handle_request("GET")

    def do_POST(self):
        self.handle_request("POST")

    def do_PUT(self):
        self.handle_request("PUT")

    def do_DELETE(self):
        self.handle_request("DELETE")

    def handle_request(self, method):
        self.extra_headers = []
        path = urlparse(self.path).path
        if not path.startswith("/api/"):
            if method != "GET":
                return self.send(405, b"", "text/plain")
            body, ctype = STATIC.get(path, STATIC["/"])
            return self.send(200, body, ctype)
        for m, pattern, fn, auth, admin in ROUTES:
            match = pattern.match(path)
            if match and m == method:
                break
        else:
            return self.send_json(404, {"error": "Page introuvable."})
        con = db()
        try:
            body = {}
            if method != "GET":
                # Exiger du JSON bloque aussi les formulaires envoyés depuis un autre site (CSRF).
                if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                    raise ApiError(415, "Requête invalide.")
                length = int(self.headers.get("Content-Length") or 0)
                if length > 65536:
                    raise ApiError(413, "Requête trop grande.")
                try:
                    body = json.loads(self.rfile.read(length) or b"{}")
                except ValueError:
                    raise ApiError(400, "Requête invalide.")
                if not isinstance(body, dict):
                    raise ApiError(400, "Requête invalide.")
            user = self.current_user(con)
            if auth and not user:
                raise ApiError(401, "Veuillez vous reconnecter.")
            if admin and not user["is_admin"]:
                raise ApiError(403, "Réservé à l'administrateur.")
            result = fn(self, con, user, body, *match.groups())
            con.commit()
            self.send_json(200, {"ok": True} if result is None else result)
        except ApiError as e:
            self.send_json(e.status, {"error": e.message})
        except sqlite3.IntegrityError:
            self.send_json(409, {"error": "Ce prénom est déjà utilisé."})
        except Exception:
            traceback.print_exc()
            self.send_json(500, {"error": "Le serveur a rencontré un problème. Réessayez."})
        finally:
            con.close()

    def session_token(self):
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        return cookie["session"].value if "session" in cookie else ""

    def current_user(self, con):
        token = self.session_token()
        if not token:
            return None
        return con.execute("SELECT u.id, u.name, u.is_admin, u.changes_seen FROM sessions s JOIN users u ON u.id = s.user_id"
                           " WHERE s.token_hash = ?", (token_hash(token),)).fetchone()

    def start_session(self, con, user_id):
        token = secrets.token_urlsafe(32)
        con.execute("INSERT INTO sessions (token_hash, user_id) VALUES (?, ?)", (token_hash(token), user_id))
        self.set_cookie(token, 400 * 24 * 3600)  # longue durée : on ne redemande pas le code à chaque visite

    def set_cookie(self, value, max_age):
        secure = "; Secure" if self.headers.get("X-Forwarded-Proto") == "https" else ""
        self.extra_headers.append(
            ("Set-Cookie", "session=%s; Path=/; Max-Age=%d; HttpOnly; SameSite=Lax%s" % (value, max_age, secure)))

    def send_json(self, status, data):
        self.send(status, json.dumps(data).encode(), "application/json; charset=utf-8")

    def send(self, status, body, ctype):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; img-src 'self' data:; style-src 'self' https://fonts.googleapis.com;"
                         " font-src https://fonts.gstatic.com; frame-ancestors 'none'; base-uri 'none'")
        for name, value in getattr(self, "extra_headers", []):
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)


def main():
    init_db()
    if sys.argv[1:2] == ["reset-code"]:
        if len(sys.argv) != 4:
            sys.exit("Usage : server.py reset-code <prénom> <nouveau code>")
        try:
            code = new_code({"code": sys.argv[3]})
        except ApiError as e:
            sys.exit(e.message)
        con = db()
        changed = con.execute("UPDATE users SET code_hash = ? WHERE name = ?", (code, sys.argv[2])).rowcount
        con.commit()
        sys.exit(0 if changed else "Prénom introuvable.")
    load_static()
    host, port = os.environ.get("CADEAUX_HOST", "0.0.0.0"), int(os.environ.get("CADEAUX_PORT", "8080"))
    print("Cadeaux de Noël : http://%s:%d (base : %s)" % (host, port, DB_PATH), flush=True)
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    main()
