# Capture Facile — Windows (v3)

Application Windows 10/11 pour les captures photo et vidéo d’écran, interface sombre et gestionnaire de fichiers.

## Mode d'emploi

- **F1** : photo d’écran au format JPEG (défaut).
- **F2** : démarrer la vidéo de l’écran ; **F2 de nouveau** : arrêter et enregistrer le MP4.
- Les deux raccourcis sont indépendants et configurables (Impr. écran, F1–F12). Choisir la même touche pour l’autre action échange les raccourcis.
- Choix du format photo PNG/JPEG et du dossier pour les captures. Par défaut, dossier Téléchargements Windows.
- La bibliothèque liste les images et vidéos MP4 récentes ; ouvrir, renommer ou supprimer.
- Un compteur REC indique la durée. Attendre la finalisation du MP4 avant de fermer.

## Notes techniques

- Vidéo MP4 H.264 à 20 images/s, **sans enregistrement audio en v3**. FFmpeg est inclus dans l'EXE via imageio-ffmpeg et PyInstaller.
- Capture vidéo du bureau Windows avec FFmpeg gdigrab. Certaines fenêtres protégées ou certains jeux en plein écran exclusif peuvent ne pas être capturés ; utiliser le mode fenêtré sans bordure.
- Sur certains jeux, Windows ou logiciels, les touches F1/F2 sont déjà affectées à des actions ; les changer dans l’application si nécessaire.
- Les paramètres sont dans `%APPDATA%\CaptureFacile\settings.json` et les fichiers dans le dossier choisi.
- Sur une mise à niveau depuis v2, les anciennes valeurs de dossier et les nouveaux réglages sont pris en compte. Par défaut v3 choisit JPEG/F1/F2.
- Les captures sont réalisées dans le dossier configuré et doivent respecter la confidentialité et les droits des contenus enregistrés.

## GitHub Actions

À chaque push sur main, compilation Windows via PyInstaller. À chaque tag vN, publication de `CaptureFacile.exe` dans GitHub Releases. Cette v3 n'a pas été exécutée sur Windows dans l'environnement de création ; GitHub Actions puis un test sur PC restent nécessaires.
