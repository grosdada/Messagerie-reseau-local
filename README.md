# Messagerie locale

Petite messagerie indépendante pour envoyer du texte et des images à tous les
PC actifs du réseau local `192.168.1.0/24`.

## Utilisation

1. Copiez entièrement le dossier `LAN-Messagerie` sur chaque PC participant.
2. Lancez `Lancer_Messagerie_Locale.bat` sur chacun d'eux.
3. Ouvrez l'adresse affichée : `http://127.0.0.1:8766`.

Les PC se détectent automatiquement ; le bandeau indique ceux qui sont
actuellement joignables. Un envoi part vers tous ces PC. Aucune installation,
aucun compte, aucun service cloud ni partage SMB ne sont nécessaires.

## Réseau et sécurité

- L'interface est réservée au navigateur du PC local (`127.0.0.1`).
- Les postes se découvrent avec UDP 37020 ; les messages utilisent TCP 8766.
- Autorisez ces deux ports entrants dans le pare-feu Windows sur le profil
  **Réseau privé** seulement. Ne les ouvrez pas sur Internet et ne créez aucune
  redirection de port sur la box.
- Les connexions sont refusées en dehors de `192.168.1.0/24` (sauf localhost).
- Les images PNG, JPEG, WebP et GIF sont limitées à 6 Mo ; les 200 derniers
  messages et leurs images sont stockés localement dans `lan_messenger_data`.

Le nom affiché est le nom Windows du PC. Pour le choisir explicitement, lancez
`python lan_messenger.py --name "Studio"` dans ce dossier.
