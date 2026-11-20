-- CareerQuest / Skill proposal review schema v1
-- New tables ONLY. Does not UPDATE existing data.
-- CQ_STATEMENT
CREATE TABLE resource_skill_proposal (
    proposal_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    submission_key CHAR(64) NOT NULL UNIQUE,
    resource_id BIGINT NOT NULL REFERENCES resource(resource_id) ON DELETE RESTRICT,
    skill_id BIGINT NOT NULL REFERENCES skill(skill_id) ON DELETE RESTRICT,
    source_document_id BIGINT NOT NULL REFERENCES source_document(source_document_id) ON DELETE RESTRICT,
    existing_resource_skill_id BIGINT REFERENCES resource_skill(resource_skill_id) ON DELETE RESTRICT,
    action VARCHAR(24) NOT NULL CHECK (action IN ('ADD', 'CONFIRM', 'CHANGE_RELATION')),
    relation_type VARCHAR(50) NOT NULL CHECK (relation_type IN ('REQUIRED', 'PREFERRED', 'RELATED', 'TAUGHT', 'VERIFIED')),
    original_relation_type VARCHAR(50),
    original_snapshot_sha256 CHAR(64),
    evidence_text TEXT NOT NULL CHECK (length(trim(evidence_text)) >= 4),
    source_sha256 CHAR(64) NOT NULL CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
    confidence NUMERIC(6,5) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    producer VARCHAR(120) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING', 'APPROVED', 'REJECTED', 'APPLIED')),
    submitted_by VARCHAR(120) NOT NULL,
    reviewed_by VARCHAR(120),
    review_note TEXT,
    reviewed_at TIMESTAMPTZ,
    applied_resource_skill_id BIGINT REFERENCES resource_skill(resource_skill_id) ON DELETE RESTRICT,
    applied_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_proposal_existing_contract CHECK (
        (action = 'ADD' AND existing_resource_skill_id IS NULL
            AND original_relation_type IS NULL AND original_snapshot_sha256 IS NULL)
        OR
        (action = 'CONFIRM' AND existing_resource_skill_id IS NOT NULL
            AND original_relation_type IS NOT NULL
            AND original_relation_type = relation_type AND original_snapshot_sha256 IS NOT NULL)
        OR
        (action = 'CHANGE_RELATION' AND existing_resource_skill_id IS NOT NULL
            AND original_relation_type IS NOT NULL
            AND original_relation_type <> relation_type AND original_snapshot_sha256 IS NOT NULL)
    )
);
-- CQ_STATEMENT
CREATE UNIQUE INDEX uq_skill_proposal_active_per_skill
ON resource_skill_proposal (resource_id, skill_id)
WHERE status IN ('PENDING', 'APPROVED');
-- CQ_STATEMENT
CREATE INDEX ix_skill_proposal_status_created
ON resource_skill_proposal (status, created_at);
-- CQ_STATEMENT
CREATE TABLE resource_skill_proposal_event (
    event_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    proposal_id BIGINT NOT NULL REFERENCES resource_skill_proposal(proposal_id) ON DELETE RESTRICT,
    event_type VARCHAR(20) NOT NULL CHECK (event_type IN ('SUBMITTED', 'APPROVED', 'REJECTED', 'APPLIED')),
    actor VARCHAR(120) NOT NULL,
    note TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- CQ_STATEMENT
CREATE INDEX ix_skill_proposal_event_proposal
ON resource_skill_proposal_event (proposal_id, event_id);
