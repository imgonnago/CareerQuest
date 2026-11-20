"""CareerQuest ResourceSkill proposal commands. No writes without a named write command."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from sqlalchemy import text

from src.db.database import engine
from src.mapping.resource_skill_proposal_service import (
    Candidate, ReviewError, preview, submit, review, apply, status, list_pending,
)


def _json(data):
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def _candidate(path: Path):
    return Candidate.from_json_dict(json.loads(path.read_text(encoding="utf-8-sig")))


def main():
    p = argparse.ArgumentParser(description="Skill mapping proposal queue (explicit approval only)")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("schema", help="Read-only schema and count check")
    pr = sub.add_parser("preview", help="Validate proposal against live DB, no writes")
    pr.add_argument("--file", type=Path, required=True)
    st = sub.add_parser("submit", help="Write only a PENDING proposal, no ResourceSkill writes")
    st.add_argument("--file", type=Path, required=True)
    st.add_argument("--actor", required=True)
    rv = sub.add_parser("review", help="Human approve/reject, does not change ResourceSkill")
    rv.add_argument("--id", type=int, required=True)
    rv.add_argument("--actor", required=True)
    rv.add_argument("--note", required=True)
    rv.add_argument("--decision", choices=["approve", "reject"], required=True)
    ap = sub.add_parser("apply", help="Explicitly write the approved mapping to ResourceSkill")
    ap.add_argument("--id", type=int, required=True)
    ap.add_argument("--actor", required=True)
    sh = sub.add_parser("show", help="Read-only proposal status + event audit")
    sh.add_argument("--id", type=int, required=True)
    pend = sub.add_parser("pending", help="Read-only pending list")
    pend.add_argument("--limit", type=int, default=20)
    args = p.parse_args()

    try:
        if args.command == "schema":
            with engine.connect() as conn:
                db = conn.execute(text("SELECT current_database()" )).scalar_one()
                exists = [conn.execute(text("SELECT to_regclass(:name) IS NOT NULL"),
                    {"name": "public." + table}).scalar_one() for table in (
                    "resource_skill_proposal", "resource_skill_proposal_event")]
                print("[DB]", db, "[SCHEMA]", dict(zip(("proposals", "events"), exists)))
                if all(exists):
                    for table in ("resource_skill_proposal", "resource_skill_proposal_event"):
                        n = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
                        print("[ROWS]", table, n)
                else:
                    print("[NEXT] python -m src.db.migrate_resource_skill_proposals --apply")
            print("[READ ONLY] No DB writes")
        elif args.command == "preview":
            _json(preview(_candidate(args.file)))
            print("[READ ONLY] No DB writes")
        elif args.command == "submit":
            c = _candidate(args.file)
            try:
                info = preview(c)
            except ReviewError as exc:
                # A retry of an already APPLIED/REJECTED request can fail live
                # preview because the underlying mapping has changed. The
                # service will accept ONLY an exact previous payload in that
                # case, otherwise live validation still blocks it.
                print(f"[LIVE PREVIEW BLOCKED] {exc}")
                print("[RETRY] Only an identical previously stored submission may be acknowledged")
            else:
                _json(info)
            token = f"SUBMIT_{c.resource_id}_{c.skill_id}"
            if input(f"Type {token} to stage PENDING proposal: ").strip() != token:
                raise SystemExit("[CANCEL] No DB writes")
            pid, state = submit(c, args.actor)
            print(f"[STAGED] proposal_id={pid}, status={state}; ResourceSkill unchanged")
        elif args.command == "review":
            if args.decision == "approve":
                token = f"APPROVE_PROPOSAL_{args.id}"
                if input(f"Human semantic review required. Type {token}: ").strip() != token:
                    raise SystemExit("[CANCEL] No DB writes")
            state = review(args.id, approve=args.decision == "approve", actor=args.actor, note=args.note)
            print(f"[REVIEWED] proposal_id={args.id}, status={state}; ResourceSkill unchanged")
        elif args.command == "apply":
            token = f"APPLY_PROPOSAL_{args.id}"
            if input(f"Will modify exactly one ResourceSkill link. Type {token}: ").strip() != token:
                raise SystemExit("[CANCEL] No DB writes")
            mid = apply(args.id, actor=args.actor)
            print(f"[APPLIED] resource_skill_id={mid}")
        elif args.command == "show":
            _json(status(args.id))
        elif args.command == "pending":
            _json(list_pending(args.limit))
    except ReviewError as err:
        raise SystemExit(f"[BLOCK] {err}") from err


if __name__ == "__main__":
    main()
