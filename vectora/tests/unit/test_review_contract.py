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


@pytest.mark.parametrize(
    ("field", "value"),
    [("delivery_id", None), ("head_sha", None), ("base_sha", None)],
)
def test_review_job_contract_accepts_explicit_null_optional_fields(
    field: str, value: None
) -> None:
    payload = {
        "job_id": "job",
        "diff": "x",
        "callback_secret": "a" * 32,
        field: value,
    }
    assert ReviewJobRequest.model_validate(payload).model_dump()[field] is None


@pytest.mark.parametrize("field", ["delivery_id", "head_sha", "base_sha"])
def test_review_job_contract_rejects_empty_optional_fields(field: str) -> None:
    payload = {
        "job_id": "job",
        "diff": "x",
        "callback_secret": "a" * 32,
        field: "",
    }
    with pytest.raises(ValueError):
        ReviewJobRequest.model_validate(payload)


def test_review_job_contract_rejects_diff_over_utf8_limit() -> None:
    with pytest.raises(ValueError):
        ReviewJobRequest(job_id="job", diff="😀" * 1_500_001, callback_secret="a" * 32)
