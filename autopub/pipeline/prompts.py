"""Étape 1 — génération des prompts via `claude -p`.

Refactor de l'ancien generate_prompts.py : même CRAFT_GUIDELINES / OUTPUT_SCHEMA
et même parsing de `structured_output.prompts`, mais sous forme de fonction
importable qui **capture la sortie dans un log** et lève des erreurs typées.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .. import config

# Schéma de sortie structurée imposé à claude -p.
OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "prompts": {
            "type": "array",
            "items": {"type": "string"},
        }
    },
    "required": ["prompts"],
}

# Consignes de fond : ce qui fait un bon prompt photo-modèle, indépendamment
# de la DA et du vêtement précis. C'est le "métier" injecté à chaque appel.
CRAFT_GUIDELINES = """\
Tu es directeur artistique ET prompt engineer spécialisé en photographie de mode.
À partir d'UNE photo de référence d'un vêtement et d'une direction artistique (DA),
tu produis {n} prompts en anglais pour un générateur d'images, chacun décrivant une
photo de MODÈLE portant CE vêtement exact.

IMPORTANT : la photo de référence sera ENVOYÉE au modèle de génération en même temps
que le prompt. Chaque prompt doit donc s'adresser à ce modèle en supposant l'image
jointe : il doit lui dire explicitement de reprendre le vêtement EXACT de l'image de
référence jointe (mêmes couleurs, motif, matière, coupe, tombé, détails), et de ne
changer que le contexte (modèle, pose, décor, lumière) — jamais le vêtement.

IMPORTANT — VISAGES / IDENTITÉ : la photo de référence sert UNIQUEMENT à reproduire
le vêtement. Si une ou plusieurs personnes y portent la tenue, leur visage, leur
identité et leurs traits NE DOIVENT JAMAIS être réutilisés. Chaque prompt doit
exiger explicitement un mannequin générique au visage inventé (formulations type
"a completely different generic professional model, do NOT reproduce the face or
identity of any person in the reference image; use only the garment from it").

NOTE UTILISATEUR : un message accompagnant parfois la photo peut préciser un détail
décisif (ex. « c'est la fille de gauche qui porte la tenue à mettre en avant », ou
le type de produit). Quand cette note est fournie, tiens-en compte pour identifier
le bon vêtement / la bonne personne-source, sans jamais copier son visage.

MÉTHODE :
1. Regarde précisément la photo de référence avec ton outil Read (en tenant compte
   de la note utilisateur si elle existe). Identifie le vêtement à mettre en avant :
   type, coupe, couleur(s) EXACTES, matière, motif (ex. dentelle/crochet), tombé,
   détails (nœud, boutons, plis). C'est la vérité-terrain : les prompts ne doivent
   jamais halluciner une variante du vêtement.
2. Lis la DA comme un document LIBRE — elle n'a aucune structure imposée, le contenu
   et la forme varient d'une marque à l'autre. Elle est la SEULE source des règles
   esthétiques. Infère de ce qu'elle exprime, où que ce soit dans le texte : casting,
   énergie, lumière, grade couleur, décor/lieux autorisés, cadrage, palette, et tous
   les anti-patterns / choses à éviter qu'elle formule (de façon explicite ou implicite).
   Ne présuppose aucune section ; n'invente pas de règles ou d'interdits absents de la
   DA, et ne laisse passer aucun de ceux qu'elle pose.
3. Invente {n} idées d'images VRAIMENT VARIÉES. Fais varier sur plusieurs axes, mais
   TOUJOURS à l'intérieur de ce que la DA autorise — c'est la DA qui décide de l'amplitude :
   - le LIEU / décor : varie-le autant que la DA le permet. Si la DA ouvre à des
     contextes (rue, nature, bord de mer, intérieur...), exploite cette latitude pour
     diversifier les lieux ; si elle fixe au contraire un décor précis (p. ex. fond
     studio uni), respecte-la et ne sors pas du studio. Ne déduis jamais un lieu qui
     contredirait la DA.
   - la pose et le geste (marche, demi-tour, rire, regard de côté, main qui ajuste...) ;
   - le cadrage (plein pied, trois-quarts, plan rapproché taille) ;
   - la composition et l'emplacement de l'espace négatif pour la typo.
   Deux prompts ne doivent pas se ressembler.
4. Convertis chaque idée en UN prompt riche et autonome.

CHAQUE PROMPT DOIT :
- être en anglais, en un seul paragraphe dense ;
- ancrer une photo PROFESSIONNELLE : vocabulaire de vrai shooting (professional fashion
  editorial, lumière maîtrisée — strobe studio, softbox, lumière naturelle ou look
  flash direct selon la scène, color-graded, fine 35mm film grain), et une référence
  matériel crédible et VARIÉE d'un prompt à l'autre (ex. "shot on Hasselblad 907X, 80mm",
  "Canon R5 + 85mm f/1.4 portrait lens", "Sony 50mm f/1.2 at f/4", "medium-format 110mm")
  cohérente avec le cadrage et le lieu choisis ;
- décrire le vêtement avec ses caractéristiques EXACTES tirées de la photo, ET demander
  explicitement au générateur de reprendre le vêtement de l'IMAGE DE RÉFÉRENCE JOINTE
  (formulations type "use the exact garment shown in the attached reference image",
  "keep its colors, lace pattern, fabric and drape strictly identical to the reference"),
  pour qu'il ne génère pas une variante hallucinée ;
- décrire le casting selon la DA (vrai visage, âge, peau réelle, attitude) — visage
  d'un mannequin générique inventé, JAMAIS celui d'une personne de la photo ;
- préciser le décor/lieu, la lumière, le grade, la pose, le cadrage, l'espace négatif
  pour la typo, et le ratio d'aspect (vertical 4:5) ;
- inclure une liste "Avoid:" construite à partir des choses à éviter que TU as inférées
  de cette DA précise (et de celles universelles : peau plastique lissée, texte/logo
  sur les vêtements). Pas d'interdits génériques recopiés s'ils ne viennent pas de la DA.

Ne renvoie que le tableau `prompts` (exactement {n} chaînes), rien d'autre.
"""


class PromptGenError(RuntimeError):
    """Échec de l'étape de génération des prompts."""


def build_prompt(image_path: Path, da_text: str, n: int, user_note: str | None = None) -> str:
    """Construit le message complet envoyé à claude -p."""
    note_block = ""
    if user_note and user_note.strip():
        note_block = (
            "\n\n=== NOTE DE L'UTILISATEUR (jointe à la photo) ===\n"
            + user_note.strip()
        )
    return (
        CRAFT_GUIDELINES.format(n=n)
        + "\n\n=== PHOTO DE RÉFÉRENCE (à lire avec Read) ===\n"
        + str(image_path)
        + note_block
        + "\n\n=== DIRECTION ARTISTIQUE ===\n"
        + da_text
    )


def generate_prompts(
    image_path: Path,
    da_path: Path,
    n: int,
    model: str | None = None,
    log_path: Path | None = None,
    user_note: str | None = None,
) -> list[str]:
    """Appelle claude -p et retourne la liste des prompts.

    `user_note` est le message éventuellement joint à la photo (ex. quelle
    personne porte la tenue à mettre en avant). Toute la sortie (stdout +
    stderr) est écrite dans `log_path` si fourni. Lève PromptGenError en échec.
    """
    da_text = da_path.read_text(encoding="utf-8")
    prompt = build_prompt(image_path, da_text, n, user_note)

    cmd = [
        "claude",
        "-p",
        prompt,
        "--allowedTools",
        "Read",
        "--json-schema",
        json.dumps(OUTPUT_SCHEMA),
        "--output-format",
        "json",
    ]
    if model:
        cmd += ["--model", model]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=config.CLAUDE_TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        if log_path is not None:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            out = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            log_path.write_text(f"TIMEOUT après {exc.timeout}s\n\n# STDOUT (partiel)\n{out}", encoding="utf-8")
        raise PromptGenError(
            f"claude -p a dépassé le timeout ({config.CLAUDE_TIMEOUT}s). Voir le log : {log_path}"
        ) from exc

    _write_log(log_path, cmd, result)

    if result.returncode != 0:
        raise PromptGenError(
            f"claude -p a échoué (code {result.returncode}). Voir le log : {log_path}"
        )

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise PromptGenError(f"Sortie claude illisible : {exc}. Voir le log : {log_path}") from exc

    structured = payload.get("structured_output")
    if not structured or "prompts" not in structured:
        raise PromptGenError(f"Réponse sans 'prompts'. Voir le log : {log_path}")

    prompts = structured["prompts"]
    if not isinstance(prompts, list) or not prompts:
        raise PromptGenError(f"Liste de prompts vide/invalide. Voir le log : {log_path}")

    return prompts


def _write_log(log_path: Path | None, cmd: list[str], result: subprocess.CompletedProcess) -> None:
    if log_path is None:
        return
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "w", encoding="utf-8") as f:
        f.write("# CMD (prompt tronqué)\n")
        f.write(f"returncode={result.returncode}\n\n# STDOUT\n")
        f.write(result.stdout or "")
        f.write("\n\n# STDERR\n")
        f.write(result.stderr or "")
