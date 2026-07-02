# cintre

De la photo brute au pack marketing publiable, sans apprendre aucun outil.

Un commerçant envoie **une photo d'un produit** sur Telegram ; le bot anonymise les
visages, génère des **idées de visuels** adaptées à une direction artistique, produit
des **images mannequin** fidèles au vêtement, et renvoie le tout en **album**.

## Architecture

Pipeline par job (UUID), auditable et reprenable après crash :

```
photo (Telegram/WhatsApp) → receiver → inbox (SQLite)
                          → ingress (whitelist → marque → job)
                          → worker : anonymisation → prompts (claude) → images (codex, //) → livraison album
```

- **UI-agnostique** : `cintre/channels/` sépare `Sender` (sortant, uniforme) et
  `Receiver` (entrant : Telegram *tire* en long-poll, WhatsApp est *poussé* par
  webhook). Tout message atterrit dans la table `inbox`, drainée par un ingress
  unique agnostique au canal. Ajouter un canal = un `Sender` + un `Receiver`.
- **État** : SQLite (`cintre.sqlite`) + dossier par job (`jobs/<uuid>/` : référence, DA, prompts, images, logs, usage).
- **Anonymisation** : visages masqués sur la référence avant tout appel modèle (YuNet, `cintre/models/`).
- **Marques** : DA par utilisateur, repli sur une DA générique (`da/generic.md`).

Détail des modules dans `cintre/` ; chaque fichier est commenté.

## Prérequis

- [uv](https://docs.astral.sh/uv/)
- CLI `claude` (génération des prompts) et `codex` (génération d'images), authentifiés.
- Un bot Telegram (token via [@BotFather](https://t.me/botfather)).

## Configuration

Copier `.env.example` vers `.env` :

```
TELEGRAM_BOT_TOKEN=...
```

**WhatsApp (optionnel, Meta Cloud API).** Renseigner `WHATSAPP_TOKEN`,
`WHATSAPP_PHONE_NUMBER_ID` et `WHATSAPP_VERIFY_TOKEN` (+ `WHATSAPP_APP_SECRET`
recommandé) active le canal au démarrage. Le webhook écoute sur
`WHATSAPP_WEBHOOK_PORT` (8080 par défaut) et **doit être exposé en HTTPS** pour
Meta : en dev, un tunnel (`cloudflared`/`ngrok`) ; en prod, un reverse-proxy.
Configurer l'URL `https://.../webhook` et le *verify token* côté Meta. La whitelist
identifie l'utilisateur par son numéro : `cintre-admin allow-user --channel whatsapp
--user <numéro E.164 sans +>`.

## Lancer

```bash
uv run cintre                 # démarre ingress + worker
```

Administration :

```bash
uv run cintre-admin allow-user --channel telegram --user <id> --note owner
uv run cintre-admin add-brand --slug ete --name "Été" --da da/ete.md --n 3
uv run cintre-admin assign-user --channel telegram --user <id> --brand ete
uv run cintre-admin list-jobs
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

## Configuration (extraits, `cintre/config.py`)

- `DEFAULT_N_IMAGES` : nombre d'images par défaut (3)
- `IMAGE_CONCURRENCY` : génération d'images en parallèle (3)
- `ANONYMIZE_METHOD` : `black` (défaut) / `blur` / `pixelate`
- `OWNER_USERS` : whitelist initiale (env `CINTRE_OWNER`)

## Mise en prod — reste à faire

Infra de base en place : **VPS + Cloudflare (HTTPS)**, service `cintre` déployé, landing servie.

### Canal WhatsApp (code prêt, démarche Meta restante)
- [ ] App *Meta for Developers* + *WhatsApp Business Account* créés
- [ ] **Business verification** Meta validée (SIREN / justificatifs — délai de plusieurs jours)
- [ ] Token permanent (System User), `WHATSAPP_PHONE_NUMBER_ID` récupérés
- [ ] `.env` prod renseigné : `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_VERIFY_TOKEN`, `WHATSAPP_APP_SECRET`
- [ ] Webhook `https://.../webhook` déclaré côté Meta + verify token vérifié
- [ ] Stratégie de contact validée vs. règles Meta (pas de démarchage à froid ; sinon *templates* pré-approuvés)

### Identité & présence
- [ ] Logo finalisé (en cours) puis propagé : og-image, apple-touch-icon, avatars des bots
- [ ] Compte Instagram créé (bio + lien cintre.app + premiers before/after)
- [ ] Backlinks : annuaires (Product Hunt, BetaList…), réseaux, articles

### Juridique / RGPD (au moment de l'inscription & du traitement des photos)
La landing statique ne collecte quasi rien ; le sujet RGPD se joue à l'onboarding
du commerçant et au traitement des photos (données perso + visages).
- [ ] Message/écran de consentement à l'inscription (finalité, base légale)
- [ ] Politique de confidentialité accessible (anonymisation, rétention & suppression des jobs)
- [ ] Politique de rétention des `jobs/` (durée, purge)

### Exploitation
- [ ] Sauvegarde planifiée de `cintre.sqlite` + `jobs/`
- [ ] Monitoring / alerting (échec de job, `claude`/`codex` déconnectés)
- [ ] `claude` et `codex` authentifiés sur le VPS + suivi de conso (agrégation des `usage.json`)
- [ ] Modèle de pricing / facturation décidé (onboarding manuel via `cintre-admin` en bêta)
