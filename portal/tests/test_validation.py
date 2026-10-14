"""What the request forms accept."""

from datetime import datetime, timezone

from app.domain.validation import parse_request

NOW = datetime(2026, 10, 7, 3, 0, tzinfo=timezone.utc)  # 10:00 in Jakarta

ONBOARDING = {
    "firstName": "Nadia", "lastName": "Putri", "displayName": "Nadia Putri", "email": "Nadia.Putri@Example.com",
    "userId": "nadia.putri", "jobTitle": "Backend Engineer", "department": "IT — Engineering",
    "managerName": "Sarah Wijaya", "managerEmail": "sarah@example.com", "employmentType": "permanent",
    "workerCategory": "INTERNAL", "locationType": "PUSAT",
}


def test_a_complete_onboarding_is_accepted_and_normalised():
    parsed = parse_request("ONBOARDING", ONBOARDING, NOW)
    assert parsed.ok, parsed.errors
    assert parsed.payload["email"] == "nadia.putri@example.com"
    assert parsed.payload["employmentType"] == "PERMANENT"
    assert "startDate" not in parsed.payload
    assert parsed.effective_at is None
    assert "accessProfileId" not in parsed.payload


def test_access_profile_posted_outside_form_is_ignored():
    parsed = parse_request("ONBOARDING", {**ONBOARDING, "accessProfileId": "security"}, NOW)
    assert parsed.ok and "accessProfileId" not in parsed.payload


def test_a_contract_needs_its_end_date_and_a_branch_its_name():
    parsed = parse_request("ONBOARDING", {**ONBOARDING, "employmentType": "CONTRACT", "locationType": "CABANG"}, NOW)
    assert set(parsed.errors) == {"expiredDate", "branchName"}


def test_unknown_branch_and_missing_worker_category_are_rejected():
    invalid = parse_request("ONBOARDING", {**ONBOARDING, "workerCategory": "", "locationType": "CABANG",
                                             "branchName": "Bogor"}, NOW, structured=True)
    assert set(invalid.errors) == {"workerCategory", "branchName"}
    valid = parse_request("ONBOARDING", {**ONBOARDING, "workerCategory": "EXTERNAL", "locationType": "CABANG",
                                           "branchName": "batam"}, NOW)
    assert valid.ok and valid.payload["branchName"] == "Batam"


def test_bad_values_are_named():
    parsed = parse_request("ONBOARDING", {**ONBOARDING, "userId": "-x", "email": "a@b"}, NOW)
    assert set(parsed.errors) == {"userId", "email"}


def test_dates_stay_within_reason():
    typo = parse_request("ONBOARDING", {**ONBOARDING, "employmentType": "CONTRACT", "expiredDate": "20266-01-01"}, NOW)
    assert "expiredDate" in typo.errors
    late = parse_request("ONBOARDING", {**ONBOARDING, "employmentType": "CONTRACT", "expiredDate": "2040-01-01"}, NOW)
    assert "terlalu jauh ke depan" in late.errors["expiredDate"]


def test_termination_takes_effect_the_morning_after_the_last_day():
    parsed = parse_request("TERMINATION", {"employeeId": "emp_1", "lastWorkingDate": "2026-10-10"}, NOW)
    assert parsed.ok
    assert datetime.fromisoformat(parsed.effective_at) == datetime(2026, 10, 10, 17, 0, tzinfo=timezone.utc)  # 11 Oct 00:00 WIB


def test_a_profile_update_never_takes_its_changes_from_the_form():
    form = {**ONBOARDING, "employeeId": "emp_1", "changes": "evil"}
    parsed = parse_request("PROFILE_UPDATE", form, NOW)
    assert parsed.ok and parsed.payload["changes"] == []


def test_an_unknown_kind_is_refused():
    assert not parse_request("DELETE_EVERYONE", {}, NOW).ok
