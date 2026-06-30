"""Génère un email de démarchage (.txt) pour un magasin, à partir d'un template.

Usage :
    uv run generate-email --shop "LE DRESSING" --city Chambéry \
        --rating "4,7/5 sur 24 avis" \
        --outfit "la tenue veste en cuir marron + pantalon rayé" \
        --contact "Madame, Monsieur" \
        --sender "Théo" --sender-email theo@duonlabs.com --phone "06 12 34 56 78"

Écrit emails/<magasin>.txt (objet + corps). Seul --shop est requis ; les autres
champs s'insèrent s'ils sont fournis, sinon des formulations neutres prennent le relais.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from . import config

SUBJECT_TEMPLATE = "Des visuels mode pro pour {shop}, depuis une simple photo"

BODY_TEMPLATE = """\
Bonjour{contact_part},

Je propose un service pour les boutiques de vêtements : obtenir des visuels mode de \
qualité professionnelle à partir d'une simple photo prise au téléphone.

Le principe est simple : vous photographiez un article, vous l'envoyez sur WhatsApp, \
et vous recevez des visuels prêts à publier sur Instagram, Facebook ou en publicité — \
porté par un mannequin, en studio comme en extérieur. Tout se fait en quelques \
minutes, depuis votre téléphone et en toute autonomie.

Pour vous montrer le rendu, j'ai préparé un exemple à partir d'une photo de votre \
boutique trouvée en ligne : vous trouverez l'avant / après en pièce jointe.

Le plus parlant, c'est de vous le montrer en direct : puis-je passer une quinzaine \
de minutes en boutique cette semaine pour vous faire la démo sur vos propres pièces ? \
Dites-moi un créneau qui vous arrange{phone_part}.

Belle journée,
{sender}{sender_email_part}
"""


def build_email(
    shop: str,
    contact: str | None = None,
    sender: str = "",
    sender_email: str | None = None,
    phone: str | None = None,
) -> tuple[str, str]:
    """Retourne (objet, corps) de l'email."""
    subject = SUBJECT_TEMPLATE.format(shop=shop)
    body = BODY_TEMPLATE.format(
        contact_part=f" {contact}" if contact else "",
        phone_part=f", ou appelez-moi au {phone}" if phone else "",
        sender=sender or "[Votre nom]",
        sender_email_part=f"\n{sender_email}" if sender_email else "",
    )
    return subject, body


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s or "magasin"


def main() -> None:
    parser = argparse.ArgumentParser(description="Génère un email de démarchage (.txt) pour un magasin.")
    parser.add_argument("--shop", required=True, help="Nom du magasin")
    parser.add_argument("--contact", default=None, help="Formule d'appel, ex 'Madame, Monsieur' (optionnel)")
    parser.add_argument("--sender", default="", help="Ton nom (signature)")
    parser.add_argument("--sender-email", default=None, help="Ton email (signature)")
    parser.add_argument("--phone", default=None, help="Ton téléphone (optionnel)")
    parser.add_argument("--out", type=Path, default=None, help="Chemin de sortie .txt (défaut emails/<magasin>.txt)")
    args = parser.parse_args()

    subject, body = build_email(
        shop=args.shop,
        contact=args.contact,
        sender=args.sender,
        sender_email=args.sender_email,
        phone=args.phone,
    )

    out = args.out or (config.ROOT / "emails" / f"{_slug(args.shop)}.txt")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(f"Objet : {subject}\n\n{body}", encoding="utf-8")
    print(f"[ok] email écrit : {out}")


if __name__ == "__main__":
    main()
