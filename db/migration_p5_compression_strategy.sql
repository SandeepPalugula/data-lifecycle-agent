-- ============================================================
-- Migration: P5 Compression quality scoring
-- Adds recommended compression strategy to the decisions table.
-- The strategy is selected by the pipeline at verdict time based
-- on uniqueness and utility scores, and is nullable because it
-- only applies to COMPRESS verdicts.
-- ============================================================

ALTER TABLE decisions
    ADD COLUMN compression_strategy VARCHAR(64)
        CHECK (compression_strategy IN ('summary', 'keypoints', 'qa'));

COMMENT ON COLUMN decisions.compression_strategy IS
    'Recommended compression strategy (summary/keypoints/qa). '
    'NULL for keep/delete/standdown verdicts. '
    'Set by the pipeline at verdict time based on uniqueness and utility scores.';
