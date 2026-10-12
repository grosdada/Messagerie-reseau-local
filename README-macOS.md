# Messagerie locale sur macOS

## Lancer sans installer Python

[Téléchargez le paquet Apple Silicon](releases/MessagerieLocale-macOS-arm64.zip), décompressez-le, copiez **Messagerie locale.app**
dans Applications, puis double-cliquez dessus. Cette version est compilée pour
Apple Silicon (M1 et suivants) ; Intel nécessite une compilation sur un Mac Intel.
Python et la fenêtre WebKit sont intégrés. Une petite fenêtre de chat s'ouvre ;
fermer cette fenêtre arrête le service. L'interface reste également disponible
à `http://127.0.0.1:8766` tant que l'app tourne.

Le paquet local est signé ad hoc, sans certificat Developer ID ni notarisation.
Sur un autre Mac, Gatekeeper peut le bloquer : après une tentative d'ouverture,
utilisez Réglages Système > Confidentialité et sécurité > Ouvrir quand même,
uniquement pour votre copie de confiance. Une distribution sans cette étape
nécessite un certificat Apple Developer ID et une notarisation.

## Autoriser le réseau local

1. Connectez Mac et PC au même réseau privé `192.168.1.0/24`.
2. Acceptez l'accès au **réseau local** si macOS le demande. Si refusé, activez
   Messagerie locale dans Réglages Système > Confidentialité et sécurité > Réseau local,
   puis relancez l'app.
3. Si le pare-feu demande l'autorisation, choisissez **Autoriser les connexions
   entrantes**. Dans Réglages Système > Réseau > Pare-feu > Options, ajoutez
   Messagerie locale.app et autorisez les connexions entrantes. L'option globale
   « Bloquer toutes les connexions entrantes » empêche ces échanges.
4. Sur Windows, gardez les règles existantes UDP 37020 et TCP 8766 sur le profil privé.

L'app ne désactive pas le pare-feu et ne modifie pas ses règles automatiquement.
Les autorisations macOS peuvent devoir être renouvelées après une recompilation
ad hoc. Voir les références Apple :
[pare-feu](https://support.apple.com/en-bh/guide/mac-help/mh11783/mac) et
[confidentialité réseau local](https://developer.apple.com/documentation/technotes/tn3179-understanding-local-network-privacy).

## Texte et images

Chaque message est envoyé à tous les postes détectés. Déposez une image dans la
zone de saisie, ou choisissez-la avec le sélecteur de fichier, puis cliquez sur
Envoyer à tous. PNG, JPEG, WebP et GIF : maximum 6 Mo. Les fichiers HEIC doivent
être exportés en JPEG/PNG avant envoi. Le bandeau affiche les pairs détectés ;
comptez environ 10 secondes pour la découverte, 35 secondes pour leur expiration.

Les 200 derniers messages sont conservés dans
`~/Library/Application Support/MessagerieLocale/history.json`, les images dans
le sous-dossier `media`. Les images anciennes ne sont pas automatiquement supprimées.
Les données sont séparées du paquet .app et survivent à sa mise à jour.

## Compatibilité et portée réseau

Le protocole Windows est conservé : broadcast UDP `255.255.255.255:37020`,
annonce JSON avec `type=qbuilder-lan-messenger`, `id`, `name`, `port` ;
POST HTTP `/api/receive` sur TCP 8766 avec `id`, `sender`, `text`, `created_at`,
`image` (null ou objet `name`, `type`, `data` base64). Aucun changement des PC
existants n'est requis. Le drag-and-drop déjà présent est utilisé dans WebKit.

Le serveur écoute les interfaces IPv4 pour recevoir les PC ; l'interface et
l'API d'envoi sont réservées à localhost. Réception et médias refusent les
sources hors `192.168.1.0/24` et localhost. Aucun cloud, compte, télémétrie,
UPnP ou redirection de port. Ne configurez aucune ouverture sur la box.
Les messages HTTP sont en clair sur ce réseau de confiance, comme sur Windows.

## Construire (développeur seulement)

Sur macOS avec Python 3 installé : `sh macos/build.sh` depuis le dépôt.
Le résultat est `dist/Messagerie locale.app` et un ZIP portant l'architecture.
La construction télécharge ses dépendances ; l'application finale fonctionne
sans Python installé et sans Internet. Construisez séparément sur Intel et ARM.
La version locale a été compilée avec Python 3.14 sur macOS 26 ARM64 ; les autres
versions de macOS ne sont pas encore validées.

Tests : `python3 -m unittest discover -s tests -v`.
La fixture `tests/fixtures/windows_reference.py` est la copie inchangée du fichier
Windows au commit `87c39a741a89a432a46942193954a6e41bef3959`.
Les tests échangent texte et images dans les deux sens via de vrais serveurs HTTP
localhost et vérifient le format broadcast et les limites de réseau. Ils ne
remplacent pas une validation physique Mac/PC (pare-feu, Wi-Fi, broadcast).

## Vérifier sur votre réseau

- Lancez un PC Windows existant et le Mac ; vérifiez leur présence mutuelle.
- Envoyez du texte accentué puis une image dans les deux sens, y compris une
  image déposée dans la fenêtre du Mac. Vérifiez les octets/images reçus.
- Fermez l'app Mac : le PC doit retirer le Mac après environ 35 secondes.
- Si aucun pair n'apparaît : vérifiez les IP, les autorisations ci-dessus, les
  VPN, et l'isolation des clients Wi-Fi ou le réseau invité de la box.
- Si le port 8766 est occupé, un second lancement ouvre l'interface du service
  déjà présent dans le navigateur. Fermez l'ancienne instance pour remplacer
  sa version. Si un autre logiciel utilise ce port, libérez-le.

## Validation effectuée

- Compilation ARM64 réussie et signature ad hoc vérifiée avec `codesign --verify --deep --strict`.
- Application lancée : `/api/state` répond sur 127.0.0.1:8766.
- Quatre postes réels découverts par UDP ; leurs `/api/receive` rejettent correctement un message vide avec HTTP 400, sans historique ajouté.
- Tests HTTP texte Unicode et images dans les deux sens avec la source Windows originale : 3 tests réussis.
- Contrôle visuel WebKit et dépôt de fichier manuel non effectués : autorisation de contrôle de l’interface indisponible. À vérifier sur le Mac avec la procédure ci-dessus.
