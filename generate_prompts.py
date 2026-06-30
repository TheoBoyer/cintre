"""Wrapper CLI mince autour de autopub.pipeline.prompts (debug manuel).

Usage :
    uv run generate_prompts photos/ref.jpg --da=da/fashion-mode.md --n=5
"""

import argparse
import json
import sys
from pathlib import Path

from autopub.pipeline.prompts import PromptGenError, generate_prompts


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Génère n prompts photo-modèle à partir d'une photo produit et d'une DA."
    )
    parser.add_argument("image", type=Path, help="Chemin de la photo du vêtement")
    parser.add_argument("--da", type=Path, required=True, help="Fichier de direction artistique (.md)")
    parser.add_argument("--n", type=int, default=5, help="Nombre de prompts (défaut : 5)")
    parser.add_argument("--model", default=None, help="Modèle claude (optionnel)")
    parser.add_argument("--log", type=Path, default=None, help="Fichier de log (optionnel)")
    parser.add_argument("--note", default=None, help="Message joint à la photo (optionnel)")
    args = parser.parse_args()

    if not args.image.exists():
        raise SystemExit(f"Photo introuvable : {args.image}")
    if not args.da.exists():
        raise SystemExit(f"DA introuvable : {args.da}")
    if args.n < 1:
        raise SystemExit("--n doit être >= 1")

    try:
        prompts = generate_prompts(args.image, args.da, args.n, args.model, args.log, args.note)
    except PromptGenError as exc:
        raise SystemExit(str(exc))

    json.dump(prompts, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
