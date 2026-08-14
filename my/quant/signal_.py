"""Compatibility facade for the active strategy package's signal workflow.

The implementation moved to ``my.strategies.lgb_alpha158_gate905_v1`` so the
Workflow, rolling rules, Strategy, models, and release manifests are versioned
together.  Existing shadow/parity callers intentionally keep this module path.
"""

from pathlib import Path

from my.strategies.lgb_alpha158_gate905_v1.workflow import (
    ReleaseValidationError,
    candidate_dir,
    config_sha256,
    manifest_path,
    model_path,
    promote_candidate,
    quarter_start,
    release_provenance,
    release_id,
    rolling_window,
    runtime_code_sha256,
    scores_for,
    train_candidate,
    validate_candidate_against_archive,
    validate_release,
)


def ensure_model(for_date: str, log=print) -> Path:
    """Return a verified published model; never train as a fallback."""
    published = validate_release(for_date)
    log(f"[signal] 已校验正式发布 {published.release_id}: {published.model_path.name}")
    return published.model_path


__all__ = [
    "ReleaseValidationError",
    "candidate_dir",
    "config_sha256",
    "ensure_model",
    "manifest_path",
    "model_path",
    "promote_candidate",
    "quarter_start",
    "release_provenance",
    "release_id",
    "rolling_window",
    "runtime_code_sha256",
    "scores_for",
    "train_candidate",
    "validate_candidate_against_archive",
    "validate_release",
]
