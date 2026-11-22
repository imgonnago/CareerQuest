-- CareerQuest Agent Skill Mapping / storage-only foundation v1
-- 2026-11-22: ADDITIVE schema. NO changes to resource_skill, student_skill,
-- role_skill, or the existing resource_skill_proposal workflow.
-- Neither autonomous approval nor agent execution is enabled here.
-- Run only through: python -m src.db.migrate_agent_skill_mapping_foundation --apply

-- CQ_STATEMENT
CREATE TABLE agent_skill_mapping_policy (
    policy_id SMALLINT PRIMARY KEY CHECK (policy_id = 1),
    policy_version VARCHAR(40) NOT NULL,
    allow_auto_approve BOOLEAN NOT NULL DEFAULT FALSE,
    allow_auto_apply BOOLEAN NOT NULL DEFAULT FALSE,
    default_route VARCHAR(24) NOT NULL DEFAULT 'HUMAN_REVIEW'
        CHECK (default_route IN ('HUMAN_REVIEW', 'BLOCKED')),
    max_retries SMALLINT NOT NULL DEFAULT 2
        CHECK (max_retries BETWEEN 0 AND 5),
    policy_notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_agent_policy_apply_requires_approve
        CHECK (NOT allow_auto_apply OR allow_auto_approve)
);

-- CQ_STATEMENT
INSERT INTO agent_skill_mapping_policy (
    policy_id, policy_version, allow_auto_approve, allow_auto_apply,
    default_route, max_retries, policy_notes
) VALUES (
    1, 'manual_review_v1', FALSE, FALSE, 'HUMAN_REVIEW', 2,
    'Schema only. All validated proposals still require human review; no automatic approve/apply executor exists.'
);

-- CQ_STATEMENT
CREATE TABLE agent_skill_mapping_run (
    run_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    correlation_key VARCHAR(128) UNIQUE,
    agent_name VARCHAR(120) NOT NULL,
    agent_version VARCHAR(80),
    objective VARCHAR(120) NOT NULL DEFAULT 'RESOURCE_SKILL_MAPPING',
    source_kind VARCHAR(24) NOT NULL
        CHECK (source_kind IN ('DB_RESOURCE', 'WEB_SEARCH', 'RAG', 'MANUAL')),
    resource_id BIGINT REFERENCES resource(resource_id) ON DELETE RESTRICT,
    source_document_id BIGINT REFERENCES source_document(source_document_id) ON DELETE RESTRICT,
    source_sha256 CHAR(64)
        CHECK (source_sha256 IS NULL OR source_sha256 ~ '^[0-9a-f]{64}$'),
    policy_id SMALLINT NOT NULL DEFAULT 1
        REFERENCES agent_skill_mapping_policy(policy_id) ON DELETE RESTRICT,
    mode VARCHAR(24) NOT NULL DEFAULT 'SHADOW'
        CHECK (mode IN ('SHADOW', 'ASSISTED')),
    status VARCHAR(24) NOT NULL DEFAULT 'CREATED'
        CHECK (status IN ('CREATED', 'RUNNING', 'AWAITING_REVIEW',
                         'COMPLETED', 'FAILED', 'CANCELLED')),
    max_attempts SMALLINT NOT NULL DEFAULT 3
        CHECK (max_attempts BETWEEN 1 AND 10),
    -- Identifiers and non-sensitive metadata ONLY; never passwords, full profiles,
    -- raw API request/response bodies, or unredacted student information.
    context_metadata JSONB NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(context_metadata) = 'object'),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_agent_run_source_document_needs_resource
        CHECK (source_document_id IS NULL OR resource_id IS NOT NULL),
    CONSTRAINT ck_agent_run_db_source_complete
        CHECK (source_kind <> 'DB_RESOURCE'
           OR (resource_id IS NOT NULL AND source_document_id IS NOT NULL
               AND source_sha256 IS NOT NULL)),
    CONSTRAINT ck_agent_run_time_order
        CHECK (finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at)
);

-- CQ_STATEMENT
CREATE INDEX ix_agent_mapping_run_status_created
    ON agent_skill_mapping_run(status, created_at DESC);

-- CQ_STATEMENT
CREATE INDEX ix_agent_mapping_run_resource_created
    ON agent_skill_mapping_run(resource_id, created_at DESC);

-- CQ_STATEMENT
CREATE TABLE agent_skill_mapping_attempt (
    attempt_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id BIGINT NOT NULL
        REFERENCES agent_skill_mapping_run(run_id) ON DELETE RESTRICT,
    attempt_no INTEGER NOT NULL CHECK (attempt_no > 0),
    retry_of_attempt_id BIGINT
        REFERENCES agent_skill_mapping_attempt(attempt_id) ON DELETE RESTRICT,
    candidate_skill_id BIGINT REFERENCES skill(skill_id) ON DELETE RESTRICT,
    candidate_skill_name VARCHAR(300) NOT NULL,
    relation_type VARCHAR(24)
        CHECK (relation_type IS NULL OR relation_type IN
               ('REQUIRED', 'PREFERRED', 'RELATED', 'TAUGHT', 'VERIFIED')),
    -- Public-source quote only; do not insert sensitive student/profile text.
    evidence_excerpt TEXT CHECK (evidence_excerpt IS NULL OR length(evidence_excerpt) <= 4000),
    confidence NUMERIC(6,5)
        CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1),
    validation_status VARCHAR(20) NOT NULL DEFAULT 'NOT_RUN'
        CHECK (validation_status IN ('NOT_RUN', 'PASSED', 'FAILED')),
    validation_codes JSONB NOT NULL DEFAULT '[]'::jsonb
        CHECK (jsonb_typeof(validation_codes) = 'array'),
    risk_level VARCHAR(12) NOT NULL DEFAULT 'UNKNOWN'
        CHECK (risk_level IN ('UNKNOWN', 'LOW', 'MEDIUM', 'HIGH')),
    route_decision VARCHAR(24) NOT NULL DEFAULT 'NOT_DECIDED'
        CHECK (route_decision IN ('NOT_DECIDED', 'RETRY', 'HUMAN_REVIEW',
                                 'BLOCKED', 'AUTO_ELIGIBLE')),
    -- Optional pointer to existing HUMAN-REVIEW proposal queue.
    -- This field does NOT authorize automatic approval or application.
    linked_proposal_id BIGINT
        REFERENCES resource_skill_proposal(proposal_id) ON DELETE RESTRICT,
    reviewed_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    validated_at TIMESTAMPTZ,
    routed_at TIMESTAMPTZ,
    CONSTRAINT uq_agent_mapping_attempt_run_seq UNIQUE (run_id, attempt_no),
    CONSTRAINT ck_agent_attempt_auto_candidate_needs_validation
        CHECK (route_decision <> 'AUTO_ELIGIBLE' OR validation_status = 'PASSED')
);

-- CQ_STATEMENT
CREATE INDEX ix_agent_mapping_attempt_route
    ON agent_skill_mapping_attempt(route_decision, validation_status, created_at DESC);

-- CQ_STATEMENT
CREATE TABLE agent_skill_mapping_tool_event (
    event_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id BIGINT NOT NULL
        REFERENCES agent_skill_mapping_run(run_id) ON DELETE RESTRICT,
    attempt_id BIGINT
        REFERENCES agent_skill_mapping_attempt(attempt_id) ON DELETE RESTRICT,
    event_seq INTEGER NOT NULL CHECK (event_seq > 0),
    tool_name VARCHAR(80) NOT NULL,
    tool_version VARCHAR(80),
    decision_origin VARCHAR(20) NOT NULL
        CHECK (decision_origin IN ('AGENT', 'BACKEND', 'OPERATOR')),
    tool_status VARCHAR(16) NOT NULL
        CHECK (tool_status IN ('REQUESTED', 'SUCCESS', 'FAILED', 'SKIPPED')),
    request_sha256 CHAR(64)
        CHECK (request_sha256 IS NULL OR request_sha256 ~ '^[0-9a-f]{64}$'),
    result_code VARCHAR(100),
    duration_ms INTEGER CHECK (duration_ms IS NULL OR duration_ms >= 0),
    -- Small sanitized fields only; NOT raw tool inputs or outputs.
    event_metadata JSONB NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(event_metadata) = 'object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_agent_tool_event_run_seq UNIQUE (run_id, event_seq)
);

-- CQ_STATEMENT
CREATE INDEX ix_agent_tool_event_run_time
    ON agent_skill_mapping_tool_event(run_id, created_at);

-- CQ_STATEMENT
CREATE INDEX ix_agent_tool_event_attempt
    ON agent_skill_mapping_tool_event(attempt_id, created_at);
