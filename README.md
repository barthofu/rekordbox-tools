# Rekordbox Toolbox

Boîte à outils en ligne de commande pour Rekordbox sous Windows.

## Synchroniser des playlists depuis des fichiers M3U8

Créez un fichier JSON qui associe chaque fichier M3U8 à un chemin de playlist
Rekordbox. Les chemins des fichiers M3U8 relatifs sont résolus depuis le
répertoire du fichier JSON. Les dossiers de destination sont créés au besoin.

```json
{
  "playlists": [
    {
      "m3u8": "Playlists/warmup.m3u8",
      "rekordbox_path": "House/Opening/Warm-up"
    }
  ]
}
```

Installez la toolbox, puis vérifiez les changements prévus :

```console
rbt m3u8-sync config.json --dry-run
```

Sans `--dry-run`, la commande demande confirmation avant de modifier la
bibliothèque. `--yes` permet de confirmer explicitement sans invite. Si la
playlist existe, son contenu est remplacé par les morceaux présents dans le
M3U8 et déjà importés dans Rekordbox. Les chemins de morceaux relatifs au M3U8
sont résolus depuis le répertoire du M3U8. Les morceaux introuvables sont
signalés et omis.

Fermez Rekordbox avant d'appliquer les changements.
