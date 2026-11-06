# Capture Facile — Windows

Application de captures d'écran Windows, dotée d'une interface sombre, d'un raccourci global configurable et d'une galerie d'images.

## Fonctions v1
- Captures de tous les écrans Windows d'un seul geste, y compris depuis d'autres programmes.
- Raccourci global : `Impr. écran` (défaut), `F8`, `F9` ou `F10`.
- Sauvegarde immédiate en PNG ou JPEG, dans le dossier Téléchargements de Windows ou un dossier choisi.
- Images horodatées pour éviter les écrasements.
- Aperçus des 180 images les plus récentes du dossier, ouverture, renommage, suppression avec confirmation.
- Préférences persistantes dans `%APPDATA%/CaptureFacile/settings.json`.

## Compilation
Le workflow GitHub Actions **Construire EXE Windows** compile automatiquement sur un runner Windows à chaque push sur `main`. Récupérer `CaptureFacile-Windows` dans les Artifacts du run.

Pour publier aussi l'EXE dans Releases : depuis Termux, créer et pousser un tag `v1` (voir instructions fournies avec l'archive).

## Précisions
- `Impr. écran` est une touche système : selon Windows et les jeux, elle peut déclencher leur propre comportement en parallèle. Préférer F8/F9/F10 en cas de conflit.
- Les jeux exécutés en administrateur, les modes anti-triche et certaines applications plein écran exclusif peuvent bloquer les touches globales ou les captures. Dans ces cas, essayer le mode fenêtré sans bordure.
- La capture prend le bureau complet de tous les moniteurs, sans recadrage et sans enregistrement vidéo.
- Les images supprimées depuis la galerie sont supprimées définitivement (confirmation préalable).
