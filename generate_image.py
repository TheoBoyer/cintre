"""Wrapper CLI mince autour de autopub.pipeline.images (debug manuel).

Usage :
    uv run generate_image --ref photos/ref.jpg --prompt "..." --out results/P1.png
    uv run generate_image --ref photos/ref.jpg --prompt-file p.txt --out out.png
    echo "prompt" | uv run generate_image --ref photos/ref.jpg --out out.png
"""

import argparse
import sys
from pathlib import Path

from autopub.pipeline.images import ImageGenError, generate_image


def read_prompt(args: argparse.Namespace) -> str:
    if args.prompt is not None:
        return args.prompt
    if args.prompt_file is not None:
        return Path(args.prompt_file).read_text(encoding="utf-8").strip()
    if not sys.stdin.isatty():
        return sys.stdin.read().strip()
    raise SystemExit("Aucun prompt fourni (--prompt, --prompt-file ou stdin).")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Génère une image via Codex à partir d'un prompt et d'une image de référence."
    )
    parser.add_argument("--ref", type=Path, required=True, help="Image de référence (le vêtement)")
    parser.add_argument("--out", type=Path, required=True, help="Chemin de l'image générée à écrire")
    parser.add_argument("--prompt", default=None, help="Le prompt (texte). Sinon --prompt-file ou stdin")
    parser.add_argument("--prompt-file", default=None, help="Fichier contenant le prompt")
    parser.add_argument("--model", default=None, help="Modèle Codex (optionnel)")
    parser.add_argument("--log", type=Path, default=None, help="Fichier de log (optionnel)")
    args = parser.parse_args()

    if not args.ref.exists():
        raise SystemExit(f"Image de référence introuvable : {args.ref}")

    prompt = read_prompt(args)
    if not prompt:
        raise SystemExit("Le prompt est vide.")

    try:
        generate_image(prompt, args.ref, args.out, args.model, args.log)
    except ImageGenError as exc:
        raise SystemExit(str(exc))
    print(f"[ok] image générée : {args.out.resolve()}")


if __name__ == "__main__":
    main()
