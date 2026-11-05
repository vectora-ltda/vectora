"""Módulo de verificação pós-execução e quality gates do Vectora."""

from backend.services.verification.engine import VerificationResult, run_verification
from backend.services.verification.profile_store import (
    get_verification_profile,
    save_verification_profile,
)
from backend.services.verification.snapshot import (
    compute_files_snapshot,
    hash_file_sha256,
)

__all__ = [
    "VerificationResult",
    "compute_files_snapshot",
    "get_verification_profile",
    "hash_file_sha256",
    "run_verification",
    "save_verification_profile",
]
