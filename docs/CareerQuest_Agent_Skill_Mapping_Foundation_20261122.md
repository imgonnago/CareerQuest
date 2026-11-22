# CareerQuest — Agent Skill Mapping DB foundation (2026-11-22)

## Purpose / scope

This is an **additive schema only** for the existing `inho` backend. It provides
persistent records for future agent tool use, iterative Skill mapping attempts,
validation feedback, and review routing. It does **not** add an LLM integration,
auto-approval, production HTTP endpoints, or change any existing mappings.

**Current default policy:** `manual_review_v1`, `allow_auto_approve=false`,
`allow_auto_apply=false`, `default_route=HUMAN_REVIEW`. The table is a **future
configuration placeholder**; no running service currently reads/enforces it.
The existing `resource_skill_proposal_service` remains the only intended place
for human-reviewed permanent mapping changes. It still needs real reviewer
authentication before production exposure.

## New tables

| Table | Purpose | Main links |
|---|---|---|
| `agent_skill_mapping_policy` | Versioned policy metadata (single initial row) | `policy_id=1`; auto approval/apply OFF |
| `agent_skill_mapping_run` | One future agent workflow, source type, run status, minimal context | optional `resource`, `source_document`, mandatory `policy` |
| `agent_skill_mapping_attempt` | Candidate mapping, validation feedback, risk, route, retries | `run_id`, optional `skill_id`, `retry_of_attempt_id`, `resource_skill_proposal.proposal_id` |
| `agent_skill_mapping_tool_event` | Which tool was selected, when, outcome, elapsed time | `run_id`, optional `attempt_id` |

The proposed tool interface for a **later implementation**:

- `lookup_skill_candidates`: read existing `skill` / `skill_alias`; returns candidate IDs.
- `validate_skill_mapping`: call existing backend `preview()` without writing to DB.
- `submit_skill_proposal`: call existing backend `submit()` to store a PENDING proposal.

Tool names are not hard-coded in DB: future agents may also log `search_rag`,
`search_requirements`, `read_documents`. A *future* agent loop may record failures,
retry (bounded), or assign `HUMAN_REVIEW`. `AUTO_ELIGIBLE` is only a **placeholder
route label**, not permission to approve or apply.

### State separation

- Agent run `status`: `CREATED` → `RUNNING` → `AWAITING_REVIEW` / `COMPLETED` / `FAILED` / `CANCELLED`.
- Mapping attempt `validation_status`: `NOT_RUN` / `PASSED` / `FAILED`.
- Mapping attempt `route_decision`: `NOT_DECIDED` / `RETRY` / `HUMAN_REVIEW` / `BLOCKED` / `AUTO_ELIGIBLE`.
- Existing permanent mapping proposal `status`: `PENDING` → `APPROVED` → `APPLIED` or `REJECTED`. **Not changed.**

The agent tables record **decisions and context**; they do not perform those
transitions automatically. The application layer must validate linking IDs,
that an attempt and its retry parent share a run, document belongs to resource,
reviewer authorization, and status transitions when integration is written.

## Installation — local PostgreSQL only

Download the package, expand its paths *at the CareerQuest repository root*.
It adds only these new files:

- `src/db/migrations/20261122_agent_skill_mapping_foundation.sql`
- `src/db/migrate_agent_skill_mapping_foundation.py`
- `docs/CareerQuest_Agent_Skill_Mapping_Foundation_20261122.md`

Run from the Python virtualenv with PostgreSQL running and the existing `.env`:

```powershell
# inspect only (no writes)
python -m src.db.migrate_agent_skill_mapping_foundation
# explicitly add new DB schema (asks for a typed confirmation)
python -m src.db.migrate_agent_skill_mapping_foundation --apply
# subsequent call: verify already exists, no writes
python -m src.db.migrate_agent_skill_mapping_foundation
```

This runner requires local `careerquest` DB, schema `public`, and existing
`resource_skill_proposal` migration. It creates all four tables and default
policy row inside one transaction. It refuses a partially present schema and
never uses `CREATE TABLE IF NOT EXISTS` to hide incompatible existing tables.

For Git, stage only these three paths, not `git add .`:

```powershell
git add -- src/db/migrations/20261122_agent_skill_mapping_foundation.sql src/db/migrate_agent_skill_mapping_foundation.py docs/CareerQuest_Agent_Skill_Mapping_Foundation_20261122.md
git diff --cached --check
git commit -m "feat: add agent Skill mapping DB foundation (manual review only)"
```

**Not tested:** actual LLM tool calling, auto routing, auto approval, integration
with the model-team pipeline. Those remain follow-up development work.

**Data hygiene:** `context_metadata`, `event_metadata`, and `evidence_excerpt`
are not safe places to log raw student profiles, API credentials, complete
search results, or private text. Persist only small sanitized identifiers and
public-source evidence needed for debugging and human review.
