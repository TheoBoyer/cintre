# Générer des images avec Codex en mode headless

## Prérequis

- Installer Codex CLI.
- Être connecté (`codex login`) ou avoir configuré les variables d'environnement nécessaires.
- Avoir une image de référence (optionnel).

## Générer une image

```bash
codex exec \
  --sandbox workspace-write \
  '$imagegen Génère un logo minimaliste représentant un renard et sauvegarde-le dans ./logo.png'
```

Le préfixe `$imagegen` indique à Codex d'utiliser le générateur d'images plutôt que de produire uniquement du texte.

## Utiliser une image de référence

```bash
codex exec \
  --image ./reference.png \
  --sandbox workspace-write \
  '$imagegen Utilise cette image comme référence pour créer une version en style pixel art et sauvegarde le résultat dans ./pixel_art.png'
```

Il est possible de fournir plusieurs images :

```bash
codex exec \
  --image face.png \
  --image style.png \
  --sandbox workspace-write \
  '$imagegen Utilise la première image comme sujet et la seconde comme style. Sauvegarde le résultat dans output.png.'
```

## Mode non interactif

Pour une utilisation dans un script ou un pipeline CI :

```bash
codex exec \
  --ask-for-approval never \
  --sandbox workspace-write \
  '$imagegen Génère une icône 512×512 et enregistre-la dans assets/icon.png'
```

## Conseils

- Toujours utiliser `--sandbox workspace-write` si l'image doit être enregistrée sur le disque.
- Les chemins passés à `--image` sont transmis au modèle comme images de référence.
- Décrire précisément le rendu attendu (style, résolution, fond transparent, palette, etc.) directement dans le prompt.