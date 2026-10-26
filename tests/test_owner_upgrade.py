# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
from copy import deepcopy
import pytest
from reports.owner.model import build_bim
from reports.owner.upgrade import migration_for


def test_only_exact_current_contract_or_marker_repair_is_automatic():
    target = build_bim("owner", "server", "db")
    assert migration_for(target, target) == "none"
    existing = deepcopy(target)
    existing["model"]["annotations"] = [a for a in existing["model"]["annotations"]
                                       if a["name"] != "OwnerContractVersion"]
    assert migration_for(existing, target) == "restore-contract-3-marker"


def rebound(bim, other_hash="0" * 63 + "1", rules='"OLD-001"'):
    """The same model as an earlier release built it: another rule-logic hash and rule list pinned."""
    import re
    old = deepcopy(bim)
    swap = lambda text: re.sub(r"(rule_id\] IN )\{[^{}]*\}", lambda m: m.group(1) + "{" + rules + "}",
                               re.sub(r'(profile_sha256\] ={1,2} )"[0-9a-f]{64}"', lambda m: m.group(1) + '"' + other_hash + '"', text))
    for permission in old["model"]["roles"][0]["tablePermissions"]:
        permission["filterExpression"] = swap(permission["filterExpression"])
    for table in old["model"]["tables"]:
        for measure in table.get("measures", []):
            measure["expression"] = swap(measure["expression"])
    return old


def test_a_model_built_for_another_rule_logic_version_is_rebound_automatically():
    target = build_bim("owner", "server", "db")
    existing = rebound(target)
    assert existing != target
    assert migration_for(existing, target) == "refresh-profile-binding"
    assert migration_for(rebound(target, "f" * 64, '"A", "B"'), target) == "refresh-profile-binding"


def test_a_filter_that_lost_its_profile_condition_is_never_treated_as_a_rebinding():
    target = build_bim("owner", "server", "db")
    for drop in ("profile_sha256", "profile_id", "rule_id"):
        existing = rebound(target)
        for permission in existing["model"]["roles"][0]["tablePermissions"]:
            lines = permission["filterExpression"].split("\n")
            permission["filterExpression"] = "\n".join(line for line in lines if drop not in line)
        with pytest.raises(ValueError, match="No approved owner migration"):
            migration_for(existing, target)
    existing = rebound(target)
    existing["model"]["roles"][0]["tablePermissions"][0]["filterExpression"] = "TRUE()"
    with pytest.raises(ValueError, match="No approved owner migration"):
        migration_for(existing, target)


@pytest.mark.parametrize("change", ["legacy", "role", "source", "column"])
def test_legacy_schema_or_security_drift_is_not_automatically_overwritten(change):
    target = build_bim("owner", "server", "db")
    existing = deepcopy(target)
    if change == "legacy":
        next(a for a in existing["model"]["annotations"] if a["name"] == "OwnerContractVersion")["value"] = "2"
    elif change == "role":
        existing["model"]["roles"] = []
    elif change == "source":
        existing["model"]["expressions"][0]["expression"] = 'Sql.Database("foreign", "db")'
    else:
        existing["model"]["tables"][0]["columns"].append({"name": "expression", "dataType": "string"})
    with pytest.raises(ValueError, match="No approved owner migration"):
        migration_for(existing, target)
