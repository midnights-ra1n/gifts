# Cadeaux de Noël

Petite application web (PWA) pour les listes de cadeaux en famille : chacun note ses envies, les autres réservent ce qu'ils offrent (seul ou en cadeau commun), avec un budget conseillé par famille. La personne qui reçoit ne voit jamais qui offre quoi.

Aucune dépendance : Python 3.9+ (bibliothèque standard) et SQLite.

## Construire le paquet

```sh
python3 build_deb.py        # produit cadeaux_1.0.0_all.deb (marche aussi sous macOS)
```

## Installer dans le conteneur LXC (Debian / Ubuntu)

```sh
apt install ./cadeaux_1.0.0_all.deb
```

Le service `cadeaux` démarre sur le port **8080**. Ouvrez `http://<ip-du-conteneur>:8080` : le premier écran crée votre compte administrateur et votre famille.

| Quoi | Où |
|---|---|
| Base de données | `/var/lib/cadeaux/cadeaux.db` |
| Réglages (port, adresse) | `/etc/default/cadeaux` puis `systemctl restart cadeaux` |
| Journaux | `journalctl -u cadeaux` |

`apt remove` garde les données ; `apt purge` les efface.

## Installer sur les téléphones (PWA)

L'installation « Ajouter à l'écran d'accueil » exige **HTTPS**. Placez l'application derrière un reverse proxy avec certificat (Caddy, Nginx Proxy Manager, Traefik…) qui transmet l'en-tête `X-Forwarded-Proto: https` (le cookie de session passe alors en `Secure`). En HTTP simple, le site fonctionne dans le navigateur mais ne s'installe pas comme une appli.

## Sauvegarde

```sh
sqlite3 /var/lib/cadeaux/cadeaux.db ".backup /root/cadeaux-$(date +%F).db"
```

(ou copiez le fichier service arrêté).

## Code secret oublié

L'administrateur peut changer le code de n'importe qui dans **Réglages**. S'il a oublié le sien :

```sh
runuser -u cadeaux -- env CADEAUX_DB=/var/lib/cadeaux/cadeaux.db python3 /opt/cadeaux/server.py reset-code "Prénom" 1234
```

## Développement

```sh
python3 server.py                     # http://localhost:8080, base cadeaux.db dans le dossier courant
python3 -m unittest test_server       # tests de l'API
```
# gifts
# gifts
# gifts
