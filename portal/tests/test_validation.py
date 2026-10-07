"""What the request forms accept."""

from datetime import datetime, timezone

from app.domain.validation import parse_request

NOW = datetime(2026, 10, 7, 3, 0, tzinfo=timezone.utc)  # 10:00 in Jakarta

ONBOARDING = {
    "firstName": "Nadia", "lastName": "Putri", "displayName": "Nadia Putri", "email": "Nadia.Putri@Example.com",
    "userId": "nadia.putri", "jobTitle": "Backend Engineer", "department": "IT — Engineering",
    "managerName": "Sarah Wijaya", "managerEmail": "sarah@example.com", "employmentType": "permanent",
    "locationType": "PUSAT",
}


def test_a_complete_onboarding_is_accepted_and_normalised():
    parsed = parse_request("ONBOARDING", ONBOARDING, NOW)
    assert parsed.ok, parsed.errors
    assert parsed.payload["email"] == "nadia.putri@example.com"
    assert parsed.payload["employmentType"] == "PERMANENT"
    assert parsed.payload["startDate"] == "2026-10-07"
    assert parsed.effective_at is None
    assert "accessProfileId" not in parsed.payload  # empty values are left out


def test_a_contract_needs_its_end_date_and_a_branch_its_name():
    parsed = parse_request("ONBOARDING", {**ONBOARDING, "employmentType": "CONTRACT", "locationType": "CABANG"}, NOW)
    assert set(parsed.errors) == {"expiredDate", "branchName"}


def test_bad_values_are_named():
    parsed = parse_request("ONBOARDING", {**ONBOARDING, "userId": "-x", "email": "a@b", "accessProfileId": "root"}, NOW)
    assert set(parsed.errors) == {"userId", "email", "accessProfileId"}


def test_dates_stay_within_reason():
    typo = parse_request("ONBOARDING", {**ONBOARDING, "startDate": "20266-01-01"}, NOW)
    assert "startDate" in typo.errors
    late = parse_request("ONBOARDING", {**ONBOARDING, "startDate": "2030-01-01"}, NOW)
    assert "terlalu jauh ke depan" in late.errors["startDate"]


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
