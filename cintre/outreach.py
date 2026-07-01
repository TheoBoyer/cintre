"""Génère un email de démarchage pour un magasin, à partir d'un template.

Usage :
    uv run generate-email --shop "LE DRESSING" --contact "Madame, Monsieur" \
        --sender "Théo" --phone "07 89 56 87 58" \
        --before jobs/<id>/reference.jpg \
        --after jobs/<id>/images/01.png jobs/<id>/images/02.png jobs/<id>/images/03.png

Écrit toujours emails/<magasin>.txt. Si --before et --after sont fournis, écrit
aussi emails/<magasin>.html avec les images avant/après EMBARQUÉES (base64) :
on ouvre le .html dans le navigateur, on copie-colle dans Gmail, les visuels
suivent — au lieu d'une pièce jointe que personne n'ouvre.
"""

from __future__ import annotations

import argparse
import base64
import mimetypes
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


def _data_uri(path: Path, max_dim: int = 720, quality: int = 82) -> str:
    """Encode une image en data-URI base64, downscalée + recompressée en JPEG
    pour garder le HTML léger (l'email pèse alors quelques centaines de Ko)."""
    import cv2  # dépendance déjà présente (opencv-python)

    img = cv2.imread(str(path))
    if img is None:  # repli : encode le fichier brut tel quel
        mime = mimetypes.guess_type(str(path))[0] or "image/png"
        return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"

    h, w = img.shape[:2]
    scale = min(1.0, max_dim / max(h, w))
    if scale < 1.0:
        img = cv2.resize(img, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        mime = mimetypes.guess_type(str(path))[0] or "image/png"
        return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"
    return f"data:image/jpeg;base64,{base64.b64encode(buf.tobytes()).decode('ascii')}"


def build_html(subject: str, body: str, before: Path, afters: list[Path]) -> str:
    """Email HTML autonome : le corps + un bloc avant/après images inline."""
    paragraphs = "".join(
        f'<p style="margin:0 0 16px;">{p.strip().replace(chr(10), "<br>")}</p>'
        for p in body.strip().split("\n\n")
    )
    after_imgs = "".join(
        f'<img src="{_data_uri(p)}" alt="Visuel généré" '
        f'style="width:180px;border-radius:8px;margin:0 8px 8px 0;vertical-align:top;">'
        for p in afters
    )
    before_img = (
        f'<img src="{_data_uri(before)}" alt="Photo d\'origine" '
        f'style="width:180px;border-radius:8px;vertical-align:top;">'
    )
    return f"""\
<!doctype html>
<html lang="fr">
<head><meta charset="utf-8"><title>{subject}</title></head>
<body style="margin:0;padding:24px;background:#f4f1ea;
             font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;
             color:#22201c;line-height:1.5;">
  <div style="max-width:600px;margin:0 auto;background:#fff;border-radius:12px;
              padding:28px 32px;">
    {paragraphs}
    <div style="margin-top:8px;padding-top:20px;border-top:1px solid #ece7da;">
      <p style="margin:0 0 6px;font-weight:600;">Avant — la photo d'origine</p>
      {before_img}
      <p style="margin:18px 0 6px;font-weight:600;">Après — générés par IA en quelques minutes</p>
      <div>{after_imgs}</div>
    </div>
  </div>
</body>
</html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Génère un email de démarchage (.txt) pour un magasin.")
    parser.add_argument("--shop", required=True, help="Nom du magasin")
    parser.add_argument("--contact", default=None, help="Formule d'appel, ex 'Madame, Monsieur' (optionnel)")
    parser.add_argument("--sender", default="", help="Ton nom (signature)")
    parser.add_argument("--sender-email", default=None, help="Ton email (signature)")
    parser.add_argument("--phone", default=None, help="Ton téléphone (optionnel)")
    parser.add_argument("--before", type=Path, default=None, help="Photo d'origine (pour la version HTML)")
    parser.add_argument(
        "--after", type=Path, nargs="+", default=None,
        help="Images générées (pour la version HTML)",
    )
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
    print(f"[ok] email texte : {out}")

    # Version HTML avec images embarquées si on a fourni avant + après.
    if args.before and args.after:
        for p in [args.before, *args.after]:
            if not p.exists():
                raise SystemExit(f"image introuvable : {p}")
        html = build_html(subject, body, args.before, list(args.after))
        html_out = out.with_suffix(".html")
        html_out.write_text(html, encoding="utf-8")
        print(f"[ok] email HTML  : {html_out}  (objet : {subject})")
    elif args.before or args.after:
        print("note : fournis --before ET --after pour générer la version HTML.")


if __name__ == "__main__":
    main()
