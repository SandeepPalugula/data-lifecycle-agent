-- ============================================================
-- Migration: R3 Confidence scores on verdicts
-- Adds confidence_score to the decisions table.
-- Nullable float 0.0-1.0. NULL for decisions created before R3.
-- ============================================================

ALTER TABLE decisions
    ADD COLUMN confidence_score FLOAT
        CHECK (confidence_score IS NULL OR (confidence_score >= 0 AND confidence_score <= 1));

COMMENT ON COLUMN decisions.confidence_score IS
    'Confidence in the verdict, 0.0-1.0. Computed from score quality '
    '(API vs fallback), signal strength (distance from 0.5 midpoint), '
    'and economic clarity (decisiveness of net saving). '
    'NULL for decisions created before R3 was deployed.';
