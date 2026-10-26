# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
import pytest
from analyzers.far_profile import PROFILE_SHA256
from reports.owner.model import build_bim, ACCESS_TABLE, WORKSPACE_TABLE
from reports.owner.schema import OWNER_TABLES


@pytest.mark.parametrize("table", [WORKSPACE_TABLE if t.name == "owner_workspaces" else t.name for t in OWNER_TABLES])
def test_every_owner_table_has_independent_expiring_id_scoped_rls(table):
    model = build_bim("owner", "server", "db")["model"]
    role = model["roles"][0]
    predicate = next(p["filterExpression"] for p in role["tablePermissions"] if p["name"] == table)
    for term in ("USEROBJECTID()", "UTCNOW()", "expires_at", "refreshed_at", "_principal"):
        assert term in predicate
    if table != ACCESS_TABLE:
        assert "workspace_id" in predicate and "COUNTROWS(FILTER(owner_access" in predicate
    if table not in {WORKSPACE_TABLE, ACCESS_TABLE}:
        assert PROFILE_SHA256 in predicate


def test_owner_model_has_no_global_score_or_permission_expansion():
    model = build_bim("owner", "server", "db")["model"]
    assert len(model["tables"]) == 6
    assert model["directLakeBehavior"] == "directLakeOnly"
    assert len(model["roles"]) == 1 and "members" not in model["roles"][0]
    assert next(t for t in model["tables"] if t["name"] == ACCESS_TABLE)["isHidden"]
    measures = [m for t in model["tables"] for m in t.get("measures", [])]
    assert not any(m["name"] == "Architecture Score" for m in measures)
    assert all("REMOVEFILTERS" not in m["expression"] for m in measures)
    assert any("is_latest" in m["expression"] and PROFILE_SHA256 in m["expression"] for m in measures)
    assert all(r["crossFilteringBehavior"] == r["securityFilteringBehavior"] == "oneDirection"
               for r in model["relationships"])
