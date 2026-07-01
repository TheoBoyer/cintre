"""Configuration centrale : chemins, valeurs par défaut, textes utilisateur.

Aucune logique ici — uniquement des constantes. Tout le reste du package importe
depuis ce module pour rester cohérent.
"""

from __future__ import annotations

import os
from pathlib import Path

# --- Racine projet & chemins ------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
JOBS_DIR = ROOT / "jobs"
DB_PATH = ROOT / "cintre.sqlite"
DA_DIR = ROOT / "da"

# --- Marque par défaut ------------------------------------------------------
# DA générique « assez bonne » pour n'importe quelle boutique. Une marque
# dédiée (ex. fashion-mode) peut être créée puis assignée via cintre-admin.
DEFAULT_BRAND_ID = "generic"
DEFAULT_BRAND_NAME = "Générique"
DEFAULT_DA_PATH = DA_DIR / "generic.md"
DEFAULT_N_IMAGES = 3

# --- Sécurité : whitelist ---------------------------------------------------
# Comptes autorisés à envoyer des requêtes, seedés au démarrage.
# Format : (channel, user_ref). Surchargé via la variable d'env CINTRE_OWNER
# (ex "telegram:856243729,telegram:123").
def _owner_users() -> list[tuple[str, str]]:
    raw = os.environ.get("CINTRE_OWNER", "telegram:856243729")
    users: list[tuple[str, str]] = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        channel, _, user_ref = token.partition(":")
        if channel and user_ref:
            users.append((channel.strip(), user_ref.strip()))
    return users


OWNER_USERS = _owner_users()

# --- Anonymisation des visages (sur la photo de référence) ------------------
ANONYMIZE_ENABLED = True
ANONYMIZE_METHOD = "black"     # 'black' (recommandé) | 'blur' | 'pixelate'
ANON_PAD = 0.2                 # marge ajoutée autour de chaque visage détecté
ANON_SCORE_THRESHOLD = 0.6     # seuil de confiance YuNet

# --- Timeouts des sous-process modèle (évite un worker figé) ---------------
CLAUDE_TIMEOUT = 300          # génération des prompts (s)
CODEX_TIMEOUT = 900           # génération d'une image (s)

# --- Parallélisme de génération d'images (dans un même job) ----------------
# Borné pour ne pas saturer le quota Codex / les rate limits. 1 = séquentiel.
IMAGE_CONCURRENCY = 3

# --- Worker / reprise -------------------------------------------------------
LEASE_SECONDS = 1800          # durée d'un bail worker (30 min)
MAX_ATTEMPTS = 2              # tentatives avant échec définitif d'un job
WORKER_IDLE_SLEEP = 2.0       # pause de la boucle worker quand la file est vide
INGRESS_POLL_TIMEOUT = 30     # long-poll Telegram (s)

# --- Ingress / inbox --------------------------------------------------------
INGRESS_DRAIN_BATCH = 50      # nombre de messages drainés par tour
INGRESS_DRAIN_SLEEP = 1.0     # pause quand l'inbox est vide (s)

# --- WhatsApp (Meta Cloud API) ----------------------------------------------
# Secrets lus depuis l'environnement au démarrage (cf. app.py) : WHATSAPP_TOKEN,
# WHATSAPP_PHONE_NUMBER_ID, WHATSAPP_VERIFY_TOKEN, WHATSAPP_APP_SECRET.
WHATSAPP_GRAPH_VERSION = "v21.0"
WHATSAPP_WEBHOOK_HOST = "0.0.0.0"   # surchargé par WHATSAPP_WEBHOOK_HOST
WHATSAPP_WEBHOOK_PORT = 8080        # surchargé par WHATSAPP_WEBHOOK_PORT

# --- Textes envoyés à l'utilisateur ----------------------------------------
ACK_TEXT = "📸 Bien reçu ! Ton pack marketing arrive sous ~15 min."
REJECT_TEXT = "⏳ Tu as déjà une demande en cours — celle-ci est ignorée. Patiente jusqu'à la livraison."
FAILED_TEXT = "😕 Désolé, la génération a échoué. Tu peux renvoyer une photo pour réessayer."
DELIVERY_CAPTION = "✨ Ton pack marketing est prêt !"
NEED_PHOTO_TEXT = "Envoie-moi une photo de produit 📸"

# Réponse aux non-autorisés : silence par défaut (ne révèle pas le bot).
DENY_TEXT: str | None = None
