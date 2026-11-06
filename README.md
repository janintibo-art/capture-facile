# Capture Facile — Windows (v4)

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

## v4 — Choix du moteur de capture photo

- Menu « MOTEUR PHOTO » : Pillow (API Windows), MSS (GDI), DXGI (DirectX).
- Menu « ÉCRANS » : tous les écrans ou écran principal. Le mode DXGI v4 capture uniquement l'écran principal : choisir cette cible pour le tester.
- F1 capture la photo selon le moteur choisi ; F2 garde l'enregistrement vidéo MP4 avec FFmpeg (gdigrab).
- Les méthodes capturent uniquement les surfaces autorisées par Windows ; aucune n'est conçue pour contourner une protection DRM.
- Si une méthode échoue, un message d'erreur explique le problème. Les préférences sont sauvegardées.
- Les dépendances `mss` et `dxcam` sont intégrées à l'EXE via PyInstaller.

## v5 — Capture Native + encodage vidéo NVIDIA expérimental

- Nouveau choix **Capture Native (C++ / GDI)**, compilé en DLL x64 par GitHub Actions et intégré à l'EXE.
- Moteur Win32 BitBlt natif, indépendant du code de capture Pillow/MSS/DXGI ; toujours dépendant des API Windows et des autorisations graphiques.
- Fonctionne avec écran principal et bureau multi-écrans.
- Nouveau choix **CPU (H.264)** (défaut) ou **NVIDIA NVENC (GPU)** pour l'encodage vidéo.
- NVENC est expérimental : l'FFmpeg fourni par imageio-ffmpeg peut ne pas contenir ce codec. Dans ce cas un message vous invite à choisir CPU ; le moteur vidéo reste `gdigrab`.
- Les flux protégés (DRM) ne seront pas récupérés par le moteur natif.
- **Vérification effectuée :** syntaxe Python et structure YAML, pas de compilation Windows locale.
