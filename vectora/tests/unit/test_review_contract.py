import pytest

from backend.services.gateway.review_contract import ReviewJobRequest


def test_review_job_contract_accepts_required_payload() -> None:
    job = ReviewJobRequest(
        job_id="job-1",
        diff="diff --git a/a b/a",
        callback_secret="a" * 32,
        delivery_id="delivery-1",
        head_sha="a" * 40,
        base_sha="b" * 40,
    )
    assert job.job_id == "job-1"


@pytest.mark.parametrize(
    "payload",
    [
        {"job_id": "job", "diff": "x", "callback_secret": ""},
        {
            "job_id": "job",
            "diff": "x",
            "callback_secret": "a" * 32,
            "head_sha": "invalid",
        },
    ],
)
def test_review_job_contract_rejects_invalid_identity(payload: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        ReviewJobRequest.model_validate(payload)
