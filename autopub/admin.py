"""CLI d'administration : whitelist et marques.

Exemples :
    uv run python -m autopub.admin allow-user --channel telegram --user 856243729 --note owner
    uv run python -m autopub.admin deny-user  --channel telegram --user 856243729
    uv run python -m autopub.admin list-allowed
    uv run python -m autopub.admin add-brand --slug ete --name "Été" --da da/ete.md --n 6
    uv run python -m autopub.admin assign-user --channel telegram --user 856243729 --brand ete
"""

from __future__ import annotations

import argparse
from pathlib import Path

from . import db
from .models import Brand


def main() -> None:
    parser = argparse.ArgumentParser(description="Administration auto-pub")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("allow-user", help="Autoriser un utilisateur")
    p.add_argument("--channel", required=True)
    p.add_argument("--user", required=True)
    p.add_argument("--note", default=None)

    p = sub.add_parser("deny-user", help="Retirer un utilisateur de la whitelist")
    p.add_argument("--channel", required=True)
    p.add_argument("--user", required=True)

    sub.add_parser("list-allowed", help="Lister les utilisateurs autorisés")

    p = sub.add_parser("add-brand", help="Créer/maj une marque")
    p.add_argument("--slug", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--da", required=True, type=Path)
    p.add_argument("--n", type=int, default=6)

    p = sub.add_parser("assign-user", help="Associer un utilisateur à une marque")
    p.add_argument("--channel", required=True)
    p.add_argument("--user", required=True)
    p.add_argument("--brand", required=True)

    p = sub.add_parser("unassign-user", help="Retirer le mapping (retour à la marque par défaut)")
    p.add_argument("--channel", required=True)
    p.add_argument("--user", required=True)

    p = sub.add_parser("cancel-job", help="Annuler un job (ne sera plus repris). Stoppe l'app d'abord !")
    p.add_argument("--id", required=True)

    p = sub.add_parser("list-jobs", help="Lister les jobs non terminés")

    args = parser.parse_args()
    conn = db.connect()
    db.init_schema(conn)

    if args.cmd == "allow-user":
        db.allow_user(conn, args.channel, args.user, args.note)
        print(f"autorisé : {args.channel}:{args.user}")
    elif args.cmd == "deny-user":
        db.deny_user(conn, args.channel, args.user)
        print(f"retiré : {args.channel}:{args.user}")
    elif args.cmd == "list-allowed":
        for row in db.list_allowed(conn):
            print(f"{row['channel']}:{row['user_ref']}  {row['note'] or ''}".rstrip())
    elif args.cmd == "add-brand":
        if not args.da.exists():
            raise SystemExit(f"DA introuvable : {args.da}")
        db.upsert_brand(conn, Brand(args.slug, args.name, args.da, args.n))
        print(f"marque enregistrée : {args.slug} (n={args.n}, da={args.da})")
    elif args.cmd == "assign-user":
        if db.get_brand(conn, args.brand) is None:
            raise SystemExit(f"marque inconnue : {args.brand}")
        db.assign_user_brand(conn, args.channel, args.user, args.brand)
        print(f"{args.channel}:{args.user} → marque {args.brand}")
    elif args.cmd == "unassign-user":
        db.unassign_user_brand(conn, args.channel, args.user)
        print(f"{args.channel}:{args.user} → marque par défaut")
    elif args.cmd == "cancel-job":
        from .models import JobStatus
        job = db.get_job(conn, args.id)
        if job is None:
            raise SystemExit(f"job inconnu : {args.id}")
        db.finish_job(conn, args.id, JobStatus.FAILED, error="cancelled by admin")
        db.log_event(conn, args.id, "error", "cancelled by admin")
        print(f"job {args.id} annulé (ne sera plus repris)")
    elif args.cmd == "list-jobs":
        for r in conn.execute(
            "SELECT id,channel,user_ref,status,n_images,updated_at FROM jobs "
            "WHERE status NOT IN ('done','failed') ORDER BY updated_at DESC"
        ):
            print(dict(r))


if __name__ == "__main__":
    main()
