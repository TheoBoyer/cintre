# auto-pub

De la photo brute au pack marketing publiable, sans apprendre aucun outil.

Un commerçant envoie **une photo d'un produit** sur Telegram ; le bot anonymise les
visages, génère des **idées de visuels** adaptées à une direction artistique, produit
des **images mannequin** fidèles au vêtement, et renvoie le tout en **album**.

## Architecture

Pipeline par job (UUID), auditable et reprenable après crash :

```
photo Telegram → ingress (whitelist → marque → job)
              → worker : anonymisation → prompts (claude) → images (codex, //) → livraison album
```

- **UI-agnostique** : `autopub/channels/` (Telegram aujourd'hui ; WhatsApp/Discord/web demain).
- **État** : SQLite (`autopub.sqlite`) + dossier par job (`jobs/<uuid>/` : référence, DA, prompts, images, logs, usage).
- **Anonymisation** : visages masqués sur la référence avant tout appel modèle (YuNet, `autopub/models/`).
- **Marques** : DA par utilisateur, repli sur une DA générique (`da/generic.md`).

Détail des modules dans `autopub/` ; chaque fichier est commenté.

## Prérequis

- [uv](https://docs.astral.sh/uv/)
- CLI `claude` (génération des prompts) et `codex` (génération d'images), authentifiés.
- Un bot Telegram (token via [@BotFather](https://t.me/botfather)).

## Configuration

Copier `.env.example` vers `.env` :

```
TELEGRAM_BOT_TOKEN=...
```

## Lancer

```bash
uv run autopub                 # démarre ingress + worker
```

Administration :

```bash
uv run autopub-admin allow-user --channel telegram --user <id> --note owner
uv run autopub-admin add-brand --slug ete --name "Été" --da da/ete.md --n 3
uv run autopub-admin assign-user --channel telegram --user <id> --brand ete
uv run autopub-admin list-jobs
```

Outils annexes :

```bash
uv run generate_prompts photos/ref.jpg --da da/generic.md --n 3   # debug prompts
uv run generate_image --ref photos/ref.jpg --prompt "..." --out out.png
uv run generate-email --shop "LE DRESSING" --city Chambéry        # email de démarchage
```

## Tests

```bash
uv run pytest                  # sans crédits API (pipeline mocké)
```

## Configuration (extraits, `autopub/config.py`)

- `DEFAULT_N_IMAGES` : nombre d'images par défaut (3)
- `IMAGE_CONCURRENCY` : génération d'images en parallèle (3)
- `ANONYMIZE_METHOD` : `black` (défaut) / `blur` / `pixelate`
- `OWNER_USERS` : whitelist initiale (env `AUTOPUB_OWNER`)
