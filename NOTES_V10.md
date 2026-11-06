# Capture Facile v10 — Audio vidéo

Mise à jour incrémentale de v9. Le ZIP ne contient que `main.py`.

- F1 photo, F2 démarre/arrête la vidéo.
- 30/60/120/240 FPS, capture zone/écran entier, montage des extraits conservés.
- Nouveau choix : Sans audio / Microphone / Son du PC / PC + microphone.
- Bouton **Détecter les appareils** : détecte les sources **DirectShow** disponibles dans FFmpeg sur le PC.
- Choisir explicitement un microphone et (si disponible) une entrée **Stereo Mix**, **What U Hear**, ou un périphérique de capture audio équivalent.
- Si Stereo Mix n'est pas présent, le son du PC ne sera pas capturable par ce moteur ; utiliser « Sans audio » ou « Microphone », ou activer une source de boucle locale autorisée sous Windows.
- Les vidéos audio sont encodées en AAC 192k. Le mélange PC+micro utilise FFmpeg `amix`.
- Le moteur n'essaie pas de capturer des sons protégés ni de contourner les DRM.

**Limitation :** cette version ne fournit pas de capture loopback WASAPI automatique : l'entrée de bouclage doit être exposée par DirectShow. Pour une prochaine version : intégration WASAPI et une timeline de montage visuelle.

**Validation :** syntaxe Python et ZIP vérifiés, test FFmpeg sur Windows à faire après GitHub Actions.
