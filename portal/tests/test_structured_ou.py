"""The approved company OU tree controls writes independently of access groups."""

import pytest

from app.domain.catalog import (CatalogError, placement_from_ou, placement_ou, structured_targets)
from app.domain.lifecycle import Lifecycle, LifecycleError
from app.domain.plan import build_plan
from app.domain.validation import parse_request


BASE = "DC=corp,DC=example,DC=com"
HQ = f"OU=Kantor Pusat,{BASE}"
BRANCH = f"OU=Kantor Cabang,{BASE}"
EXTERNAL = f"OU=External,{BASE}"
ENV = {
    "AD_OU_ROUTING_MODE": "STRUCTURED",
    "AD_OU_INTERNAL_PUSAT_PARENT": HQ,
    "AD_OU_INTERNAL_CABANG_PARENT": BRANCH,
    "AD_OU_EXTERNAL_PUSAT": f"OU=Pusat,{EXTERNAL}",
    "AD_OU_EXTERNAL_CABANG_PARENT": f"OU=Cabang,{EXTERNAL}",
    "AD_OU_INTERN": f"OU=Intern,{BASE}",
    "AD_QUARANTINE_OU": f"OU=Karantina,{BASE}",
    "AD_ACCESS_GROUP_BASE": f"CN=Base,OU=Groups,{BASE}",
    "AD_ACCESS_GROUP_ENGINEERING": f"CN=Engineering,OU=Groups,{BASE}",
}


@pytest.mark.parametrize("category,location,department,city,expected", [
    ("INTERNAL", "PUSAT", "Capital Market", None, f"OU=Capital Market,{HQ}"),
    ("INTERNAL", "CABANG", "Retail", "Bandung", f"OU=Bandung,{BRANCH}"),
    ("EXTERNAL", "PUSAT", "Generic", None, f"OU=Pusat,{EXTERNAL}"),
    ("EXTERNAL", "CABANG", "Operations", "Surabaya", f"OU=Surabaya,OU=Cabang,{EXTERNAL}"),
    ("INTERN", "PUSAT", "Generic", None, f"OU=Intern,{BASE}"),
])
def test_placement_follows_category_and_location(category, location, department, city, expected):
    assert placement_ou(category, location, department, city, ENV) == expected
    inferred = placement_from_ou(expected, ENV)
    assert inferred == {"workerCategory": category, "locationType": location,
                        **({"branchName": city} if city else {})}


def test_all_27_leaves_exist_and_unknown_names_fail_closed():
    assert len(structured_targets(ENV)) == 27
    with pytest.raises(CatalogError):
        placement_ou("INTERNAL", "PUSAT", "Finance", None, ENV)
    with pytest.raises(CatalogError):
        placement_ou("EXTERNAL", "CABANG", "Generic", "Bogor", ENV)
    with pytest.raises(CatalogError):
        placement_ou("INTERN", "CABANG", "Generic", "Bandung", ENV)


def test_actual_ad_spelling_can_be_mapped_without_changing_form_labels():
    env = {**ENV, "AD_OU_NAME_MAP": "Makasar=>Makassar;Intren=>Intern"}
    assert placement_ou("EXTERNAL", "CABANG", None, "Makasar", env) == f"OU=Makassar,OU=Cabang,{EXTERNAL}"
    assert placement_ou("INTERNAL", "PUSAT", "Intren", None, env) == f"OU=Intern,{HQ}"
    assert placement_from_ou(f"OU=Makassar,OU=Cabang,{EXTERNAL}", env)["branchName"] == "Makasar"
    with pytest.raises(CatalogError):
        placement_ou("INTERNAL", "PUSAT", "Intren", None, {**ENV, "AD_OU_NAME_MAP": "Bogor=>Unknown"})
    with pytest.raises(CatalogError):
        placement_ou("INTERNAL", "CABANG", None, "Makasar", {**ENV, "AD_OU_NAME_MAP": "Makasar=>Bandung"})


def test_access_profile_does_not_override_ou():
    payload = {"kind": "ONBOARDING", "accessProfileId": "engineering", "workerCategory": "EXTERNAL",
               "locationType": "CABANG", "branchName": "Batam", "department": "Generic",
               "jobTitle": "Analyst", "managerEmail": "manager@example.com", "email": "new@example.com",
               "displayName": "New User", "firstName": "New", "lastName": "User"}
    plan = build_plan(payload, None, ENV)
    assert plan["post"]["ou"] == f"OU=Batam,OU=Cabang,{EXTERNAL}"
    assert plan["post"]["required"] == [ENV["AD_ACCESS_GROUP_BASE"], ENV["AD_ACCESS_GROUP_ENGINEERING"]]
    assert plan["steps"][0]["params"]["ou"] == plan["post"]["ou"]


def test_movement_and_profile_change_move_to_approved_ou():
    before = {"ou": f"OU=Operations,{HQ}", "enabled": True, "groups": [ENV["AD_ACCESS_GROUP_BASE"]]}
    movement = {"kind": "MOVEMENT", "toDepartment": "Retail", "toJobTitle": "Analyst",
                "toManagerEmail": "manager@example.com", "toWorkerCategory": "EXTERNAL",
                "toLocationType": "CABANG", "toBranchName": "Medan"}
    plan = build_plan(movement, before, ENV)
    assert plan["post"]["ou"] == f"OU=Medan,OU=Cabang,{EXTERNAL}"
    assert any(step["key"] == "move-ou" for step in plan["steps"])
    profile = {"kind": "PROFILE_UPDATE", "profile": {"displayName": "New User", "department": "Retail",
                                                 "jobTitle": "Analyst", "workerCategory": "EXTERNAL",
                                                 "locationType": "PUSAT"}}
    changed = build_plan(profile, before, ENV)
    assert changed["post"]["ou"] == ENV["AD_OU_EXTERNAL_PUSAT"]
    assert any(step["key"] == "move-ou" for step in changed["steps"])


def test_form_and_submission_reject_missing_or_invalid_placement():
    invalid = parse_request("MOVEMENT", {"employeeId": "emp_1", "toDepartment": "Retail",
                                              "toJobTitle": "Analyst", "toManagerName": "Manager User",
                                              "toManagerEmail": "manager@example.com"},
                            structured=True)
    assert set(invalid.errors) == {"toWorkerCategory", "toLocationType"}
    lifecycle = Lifecycle(None, None, ENV)
    with pytest.raises(LifecycleError, match="Divisi kantor pusat"):
        lifecycle._validate_placement({"kind": "ONBOARDING", "workerCategory": "INTERNAL",
                                       "locationType": "PUSAT", "department": "Finance"})


def test_termination_disables_before_moving_to_quarantine():
    plan = build_plan({"kind": "TERMINATION"}, {"enabled": True, "groups": [], "ou": HQ}, ENV)
    assert [step["key"] for step in plan["steps"]] == ["disable-account", "revoke-groups", "move-ou", "verify"]
    assert plan["post"]["ou"] == ENV["AD_QUARANTINE_OU"]
