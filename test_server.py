"""Tests de bout en bout de l'API : python3 -m unittest test_server"""
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from http.server import ThreadingHTTPServer

import server


class Client:
    """Un navigateur : ses propres cookies, donc sa propre session."""

    def __init__(self, base):
        self.base = base
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))

    def call(self, method, path, body=None, ctype="application/json"):
        data = None if method == "GET" else json.dumps(body or {}).encode()
        headers = {"Content-Type": ctype} if data is not None else {}
        req = urllib.request.Request(self.base + "/api/" + path, data=data, method=method, headers=headers)
        try:
            with self.opener.open(req) as res:
                return res.status, json.loads(res.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def ok(self, method, path, body=None):
        status, data = self.call(method, path, body)
        assert status == 200, (method, path, status, data)
        return data


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        server.ITERATIONS = 1000  # tests rapides
        server.load_static()
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.base = "http://127.0.0.1:%d" % cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    def setUp(self):  # base neuve pour chaque test
        server.DB_PATH = os.path.join(self.tmp.name, "%s.db" % self._testMethodName)
        server.init_db()
        server.FAILED_LOGINS.clear()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.tmp.cleanup()

    def login(self, name, code):
        client = Client(self.base)
        names = {n["name"]: n["id"] for n in client.ok("GET", "start")["names"]}
        client.ok("POST", "login", {"id": names[name], "code": code})
        return client

    def test_scenario_complet(self):
        alice = Client(self.base)
        self.assertTrue(alice.ok("GET", "start")["setup"])
        alice.ok("POST", "setup", {"name": "Alice", "family": "Famille A", "code": "1234"})
        self.assertEqual(alice.call("POST", "setup", {"name": "X", "family": "Y", "code": "1234"})[0], 403)
        self.assertTrue(alice.ok("GET", "start")["me"]["is_admin"])

        admin = alice.ok("GET", "admin")
        fam_a = admin["families"][0]["id"]
        alice.ok("POST", "admin/families", {"name": "Famille B", "budget": 3000})
        fam_b = [f["id"] for f in alice.ok("GET", "admin")["families"] if f["name"] == "Famille B"][0]
        alice.ok("PUT", "admin/families/%d" % fam_a, {"name": "Famille A", "budget": 5000})
        alice.ok("POST", "admin/users", {"name": "Bob", "code": "1111", "family_ids": [fam_a, fam_b]})
        alice.ok("POST", "admin/users", {"name": "Carol", "code": "2222", "family_ids": [fam_a]})
        alice.ok("POST", "admin/users", {"name": "Dave", "code": "3333", "family_ids": [fam_b]})
        self.assertEqual(alice.call("POST", "admin/users", {"name": "bob", "code": "4444"})[0], 409)
        self.assertEqual(alice.call("POST", "admin/users", {"name": "Eve", "code": "12"})[0], 400)

        bob = self.login("Bob", "1111")
        carol = self.login("Carol", "2222")
        dave = self.login("Dave", "3333")
        self.assertEqual(bob.call("GET", "admin")[0], 403)

        bob.ok("POST", "gifts", {"title": "Vélo", "price": 3000, "link": "https://exemple.fr/velo"})
        self.assertEqual(bob.call("POST", "gifts", {"title": "Piège", "link": "javascript:alert(1)"})[0], 400)
        self.assertEqual(bob.call("POST", "gifts", {"title": ""})[0], 400)
        gift = bob.ok("GET", "my-gifts")[0]["id"]

        # Carol offre ; Alice ne peut pas « offrir seule » un cadeau déjà pris, mais peut participer.
        carol.ok("POST", "gifts/%d/give" % gift, {"together": False})
        self.assertEqual(alice.call("POST", "gifts/%d/give" % gift, {"together": False})[0], 409)
        alice.ok("POST", "gifts/%d/give" % gift, {"together": True})
        carol.ok("PUT", "gifts/%d/bought" % gift, {"bought": True})

        # La surprise : Bob ne voit jamais qui offre ni si c'est acheté.
        mine = bob.ok("GET", "my-gifts")[0]
        self.assertNotIn("givers", mine)
        self.assertNotIn("bought", mine)
        bob_id = bob.ok("GET", "start")["me"]["id"]
        self.assertEqual(bob.call("GET", "people/%d" % bob_id)[0], 404)
        self.assertEqual(bob.call("POST", "gifts/%d/give" % gift, {})[0], 404)

        # Les autres voient les réservations.
        seen = carol.ok("GET", "people/%d" % bob_id)
        self.assertEqual(sorted(g["name"] for g in seen["gifts"][0]["givers"]), ["Alice", "Carol"])
        self.assertTrue(seen["gifts"][0]["bought"])
        self.assertEqual(seen["budget"], 5000)

        # Budget le plus petit des familles partagées.
        dave_view = dave.ok("GET", "people/%d" % bob_id)
        self.assertEqual(dave_view["budget"], 3000)

        purchases = carol.ok("GET", "my-purchases")
        self.assertEqual(purchases[0]["name"], "Bob")
        self.assertEqual([g["name"] for g in purchases[0]["gifts"][0]["givers"]], ["Alice", "Carol"])

        # Dave (famille B seulement) ne voit pas Carol (famille A seulement).
        carol_id = carol.ok("GET", "start")["me"]["id"]
        self.assertEqual(dave.call("GET", "people/%d" % carol_id)[0], 404)
        families = {f["name"]: f for f in dave.ok("GET", "families")}
        self.assertEqual(list(families), ["Famille B"])
        self.assertEqual(families["Famille B"]["people"][0]["free"], 0)

        # Quand tout le monde se retire, le cadeau redevient libre et « non acheté ».
        carol.ok("DELETE", "gifts/%d/give" % gift)
        self.assertEqual(carol.call("PUT", "gifts/%d/bought" % gift, {"bought": True})[0], 403)
        alice.ok("DELETE", "gifts/%d/give" % gift)
        self.assertFalse(carol.ok("GET", "people/%d" % bob_id)["gifts"][0]["bought"])

        # Seul le propriétaire modifie ou supprime sa liste.
        self.assertEqual(carol.call("PUT", "gifts/%d" % gift, {"title": "Pirate"})[0], 404)
        self.assertEqual(carol.call("DELETE", "gifts/%d" % gift)[0], 404)
        bob.ok("PUT", "gifts/%d" % gift, {"title": "Vélo rouge", "price": None})
        self.assertEqual(bob.ok("GET", "my-gifts")[0]["title"], "Vélo rouge")

        # Changer le code de Bob coupe ses sessions ; suppression de compte.
        alice.ok("PUT", "admin/users/%d" % bob_id, {"name": "Bob", "code": "9999", "family_ids": [fam_a]})
        self.assertEqual(bob.call("GET", "my-gifts")[0], 401)
        self.assertEqual(alice.call("DELETE", "admin/users/%d" % alice.ok("GET", "start")["me"]["id"])[0], 400)
        alice.ok("DELETE", "admin/users/%d" % bob_id)
        self.assertNotIn("Bob", [u["name"] for u in alice.ok("GET", "admin")["users"]])

        alice.ok("POST", "logout")
        self.assertIsNone(alice.ok("GET", "start")["me"])

    def family_of_three(self):
        alice = Client(self.base)
        alice.ok("POST", "setup", {"name": "Alice", "family": "F", "code": "1234"})
        fam = alice.ok("GET", "admin")["families"][0]["id"]
        alice.ok("POST", "admin/users", {"name": "Bob", "code": "1111", "family_ids": [fam]})
        alice.ok("POST", "admin/users", {"name": "Carol", "code": "2222", "family_ids": [fam]})
        return alice, self.login("Bob", "1111"), self.login("Carol", "2222")

    def test_cagnotte(self):
        alice, bob, carol = self.family_of_three()
        bob.ok("POST", "gifts", {"title": "Console", "price": 30000})
        gift = bob.ok("GET", "my-gifts")[0]["id"]
        carol.ok("POST", "gifts/%d/give" % gift, {"together": True, "amount": 5000})
        alice.ok("POST", "gifts/%d/give" % gift, {"together": True})
        alice.ok("POST", "gifts/%d/give" % gift, {"together": True, "amount": 10000})  # change sa participation
        self.assertEqual(alice.call("POST", "gifts/%d/give" % gift, {"amount": -5})[0], 400)
        givers = {g["name"]: g["amount"] for g in alice.ok("GET", "people/%d" % bob.ok("GET", "start")["me"]["id"])["gifts"][0]["givers"]}
        self.assertEqual(givers, {"Alice": 10000, "Carol": 5000})
        self.assertNotIn("givers", bob.ok("GET", "my-gifts")[0])

    def test_changements(self):
        alice, bob, carol = self.family_of_three()
        bob.ok("POST", "gifts", {"title": "Livre", "price": 1500})
        gift = bob.ok("GET", "my-gifts")[0]["id"]
        bob.ok("PUT", "gifts/%d" % gift, {"title": "Livre", "price": 1290})
        bob.ok("PUT", "gifts/%d" % gift, {"title": "Livre", "price": 1290})  # rien changé : pas d'événement
        carol.ok("POST", "gifts/%d/give" % gift, {})
        carol.ok("PUT", "gifts/%d/bought" % gift, {"bought": True})

        self.assertEqual(alice.ok("GET", "start")["me"]["unseen"], 4)
        seen_by_alice = alice.ok("GET", "changes")["events"]
        self.assertEqual([e["action"] for e in seen_by_alice], ["bought", "give", "edit", "add"])
        self.assertEqual(seen_by_alice[2]["detail"], "prix : 15 € → 12,90 €")
        self.assertEqual(alice.ok("GET", "start")["me"]["unseen"], 0)

        # Bob ne voit que ses propres modifications, jamais qui offre ni le « réservé par » à la suppression.
        bob.ok("DELETE", "gifts/%d" % gift)
        seen_by_bob = bob.ok("GET", "changes")["events"]
        self.assertEqual([e["action"] for e in seen_by_bob], ["delete", "edit", "add"])
        self.assertIsNone(seen_by_bob[0]["detail"])
        self.assertEqual(bob.ok("GET", "start")["me"]["unseen"], 0)
        self.assertEqual(carol.ok("GET", "changes")["events"][0]["detail"], "Il était réservé par Carol")

    def test_migration_ancienne_base(self):
        con = server.sqlite3.connect(server.DB_PATH)
        con.executescript("DROP TABLE givers; CREATE TABLE givers (gift_id INTEGER, user_id INTEGER, PRIMARY KEY (gift_id, user_id));"
                          "ALTER TABLE users DROP COLUMN changes_seen;")
        con.close()
        server.init_db()
        con = server.sqlite3.connect(server.DB_PATH)
        self.assertIn("amount", [r[1] for r in con.execute("PRAGMA table_info(givers)")])
        self.assertIn("changes_seen", [r[1] for r in con.execute("PRAGMA table_info(users)")])
        con.close()

    def test_securite(self):
        client = Client(self.base)
        # Sans JSON (formulaire d'un autre site) : refusé.
        self.assertEqual(client.call("POST", "setup", {}, ctype="application/x-www-form-urlencoded")[0], 415)
        self.assertEqual(client.call("GET", "my-gifts")[0], 401)

    def test_blocage_apres_5_erreurs(self):
        client = Client(self.base)
        with server.db() as con:
            uid = con.execute("INSERT INTO users (name, code_hash) VALUES ('Zoé', ?)", (server.hash_code("5555"),)).lastrowid
        for _ in range(5):
            self.assertEqual(client.call("POST", "login", {"id": uid, "code": "0000"})[0], 401)
        self.assertEqual(client.call("POST", "login", {"id": uid, "code": "5555"})[0], 429)

    def test_fichiers_statiques(self):
        with urllib.request.urlopen(self.base + "/") as res:
            self.assertIn(b"Cadeaux", res.read())
        with urllib.request.urlopen(self.base + "/../server.py") as res:  # pas de traversée : renvoie l'appli
            self.assertIn(b"<!doctype html>", res.read())


if __name__ == "__main__":
    unittest.main()
