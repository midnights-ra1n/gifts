# Cadeaux de Noël

Une petite application web pour organiser les cadeaux de Noël en famille. Chacun note ses envies, les autres choisissent ce qu'ils offrent (seuls ou en cagnotte), et **la personne qui reçoit ne sait jamais qui offre quoi**.

Pensée pour toute la famille, y compris les personnes peu à l'aise avec le numérique : quatre onglets, de gros boutons, des phrases simples, une connexion en touchant son prénom puis en tapant un code chiffré.

<p align="center">
  <img src="docs/connexion.png" width="200" alt="Écran de connexion : on touche son prénom">
  <img src="docs/offrir.png" width="200" alt="Liste d'une personne avec les réservations et une cagnotte">
  <img src="docs/achats.png" width="200" alt="Mes achats : total, budget et part dans la cagnotte">
  <img src="docs/changements.png" width="200" alt="Historique des changements dans les listes">
</p>

## Fonctionnalités

- **Ma liste** : on ajoute ses idées (nom, prix, lien, précisions comme la taille ou la couleur).
- **Offrir** : on parcourt les listes de sa famille. « Je l'offre » réserve un cadeau, « Lancer une cagnotte » ouvre une participation à plusieurs.
- **Cagnotte** : chacun met le montant qu'il veut, ou prend une part égale de ce qui reste. Une barre montre ce qui est réuni et ce qu'il manque.
- **Mes achats** : ce que j'offre, ma part, une case « Déjà acheté » et une alerte si le budget est dépassé.
- **Changements** : qui a ajouté, modifié ou réservé quoi, avec une pastille pour les nouveautés. Les réservations ne sont jamais montrées à la personne concernée.
- **Familles et budgets** : plusieurs familles (parents, grands-parents, cousins…), une personne peut être dans plusieurs, et chaque famille a un budget conseillé par personne.
- **PWA** : s'installe sur l'écran d'accueil du téléphone (HTTPS requis, voir plus bas), thème clair ou sombre selon le téléphone.

## Technique

| | |
|---|---|
| Serveur | Python 3.9+, **bibliothèque standard uniquement** (`http.server`, `sqlite3`) |
| Base de données | SQLite, un seul fichier |
| Interface | HTML, CSS et JavaScript sans framework ni étape de compilation |
| Paquet | `.deb` (`Architecture: all`, s'installe sur x86_64 comme sur ARM), service systemd |

```
server.py            serveur HTTP + API JSON + schéma SQLite
static/              interface (index.html, app.js, style.css, service worker, icônes)
test_server.py       tests de bout en bout de l'API
build_deb.py         construit le .deb sans dpkg-deb (marche aussi sous macOS)
packaging/           fichiers du paquet : control, scripts, service systemd, réglages
.github/workflows/   CI : tests, construction, installation de vérification, release
```

## Installation dans un conteneur LXC (Debian / Ubuntu)

Téléchargez le `.deb` depuis la page [Releases](../../releases), ou construisez-le (voir plus bas), puis :

```sh
apt install ./cadeaux_1.0.*_all.deb
```

Le service `cadeaux` démarre tout de suite sur le port **8080**. Ouvrez `http://<ip-du-conteneur>:8080` : le premier écran crée votre compte administrateur et votre première famille. Ajoutez ensuite les autres personnes depuis **votre prénom (en haut à droite) → Réglages**, et donnez à chacune son code.

| Quoi | Où |
|---|---|
| Base de données | `/var/lib/cadeaux/cadeaux.db` |
| Réglages (port, adresse d'écoute) | `/etc/default/cadeaux`, puis `systemctl restart cadeaux` |
| Journaux | `journalctl -u cadeaux` |
| Programme | `/opt/cadeaux/` |

Mise à jour : installez simplement le nouveau `.deb`. Les données sont conservées et la base est migrée automatiquement.
Désinstallation : `apt remove cadeaux` garde les données ; `apt purge cadeaux` les efface.

### HTTPS et installation sur téléphone

L'ajout à l'écran d'accueil exige **HTTPS**. Placez l'application derrière un reverse proxy avec certificat (Caddy, Nginx Proxy Manager, Traefik…) qui transmet l'en-tête `X-Forwarded-Proto: https` : le cookie de session passe alors en `Secure`. En HTTP simple, tout fonctionne dans le navigateur, mais l'application ne s'installe pas comme une appli.

Exemple Caddy :

```
cadeaux.example.fr {
    reverse_proxy 192.168.1.50:8080
}
```

### Sauvegarde

```sh
sqlite3 /var/lib/cadeaux/cadeaux.db ".backup /root/cadeaux-$(date +%F).db"
```

### Code secret oublié

L'administrateur change le code de n'importe qui dans **Réglages**. S'il a oublié le sien :

```sh
runuser -u cadeaux -- env CADEAUX_DB=/var/lib/cadeaux/cadeaux.db \
    python3 /opt/cadeaux/server.py reset-code "Prénom" 1234
```

## Sécurité et vie privée

- Codes secrets hachés (PBKDF2-SHA256), sessions en cookie `HttpOnly` / `SameSite=Lax`, jetons stockés hachés.
- Blocage de 5 minutes après 5 codes erronés pour un même compte.
- Les requêtes de modification doivent être en JSON, ce qui bloque les formulaires envoyés depuis un autre site (CSRF).
- En-têtes CSP stricts, fichiers statiques servis depuis une liste blanche, liens de cadeaux limités à `http(s)://`.
- La surprise est garantie côté serveur : votre propre liste ne vous renvoie jamais les réservations, les participations ni les achats.

Les codes de 4 chiffres suffisent pour un usage familial. Si l'application est exposée sur internet, préférez des codes plus longs (jusqu'à 12 chiffres).

## Développement

```sh
python3 server.py                 # http://localhost:8080, base cadeaux.db dans le dossier courant
python3 -m unittest test_server   # tests de l'API
python3 build_deb.py              # construit cadeaux_<version>_all.deb
```

Variables d'environnement : `CADEAUX_DB` (chemin de la base), `CADEAUX_HOST`, `CADEAUX_PORT`, et `VERSION` pour `build_deb.py`.

## Intégration continue et releases

Le workflow [`.github/workflows/deb.yml`](.github/workflows/deb.yml) s'exécute à chaque push et pull request sur un runner Ubuntu x86_64 :

1. lance les tests de l'API ;
2. construit le `.deb` ;
3. **l'installe réellement**, vérifie que le service répond, puis teste `remove` (données gardées) et `purge` (données effacées) ;
4. joint le `.deb` aux artefacts du run ;
5. sur `main` uniquement, **publie automatiquement une release** `v1.0.<n>` avec le `.deb` en pièce jointe.

Le numéro `<n>` est celui du run et ne fait qu'augmenter : `apt install` du nouveau fichier fait donc toujours une mise à jour. Pour une version marquante, changez le préfixe `1.0` dans la variable `VERSION` du workflow. Les pull requests sont testées mais ne publient rien.
