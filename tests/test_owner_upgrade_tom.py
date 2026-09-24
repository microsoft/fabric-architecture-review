# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from reports.owner.model import build_bim
from reports.owner.upgrade import _legacy_contract, _NEW_TABLES
from reports.owner.upgrade_tom import TomEditor


class Collection(list):
    @property
    def Count(self):
        return len(self)

    def __getitem__(self, key):
        if isinstance(key, str):
            return next(item for item in self if item.Name == key)
        return super().__getitem__(key)

    def Add(self, item):
        assert not any(existing.Name == item.Name for existing in self)
        self.append(item)


class Node(SimpleNamespace):
    def Clone(self):
        return deepcopy(self)


class Permission(Node):
    @property
    def Name(self):
        return self.Table.Name


class Relationship(Node):
    @property
    def FromTable(self):
        return self.FromColumn.Table

    @property
    def ToTable(self):
        return self.ToColumn.Table


def database(bim):
    model = Node(
        Tables=Collection(), Roles=Collection(), Relationships=Collection(),
        Annotations=Collection(Node(Name=a["name"], Value=a["value"])
                               for a in bim["model"]["annotations"]),
        Expressions=Collection(Node(Name=e["name"]) for e in bim["model"]["expressions"]),
        SaveChanges=Mock(),
    )
    for t in bim["model"]["tables"]:
        table = Node(Name=t["name"], base=deepcopy(t), Measures=Collection(), Partitions=Collection())
        table.Columns = Collection(Node(Name=c["name"], Table=table) for c in t["columns"])
        for p in t["partitions"]:
            table.Partitions.Add(Node(
                Name=p["name"],
                Source=Node(ExpressionSource=model.Expressions[p["source"]["expressionSource"]]),
            ))
        for m in t.get("measures", []):
            table.Measures.Add(Node(Name=m["name"], Expression=m["expression"], base=deepcopy(m)))
        model.Tables.Add(table)
    for r in bim["model"]["roles"]:
        model.Roles.Add(Node(
            Name=r["name"], base=deepcopy(r), Members=Collection(deepcopy(r.get("members", []))),
            TablePermissions=Collection(Permission(
                Table=model.Tables[p["name"]], FilterExpression=p["filterExpression"],
            ) for p in r["tablePermissions"]),
        ))
    for r in bim["model"]["relationships"]:
        model.Relationships.Add(Relationship(
            Name=r["name"], base=deepcopy(r),
            FromColumn=model.Tables[r["fromTable"]].Columns[r["fromColumn"]],
            ToColumn=model.Tables[r["toTable"]].Columns[r["toColumn"]],
        ))
    return Node(Model=model, Refresh=Mock(), base=deepcopy(bim))


def serialize(db, options):
    result = deepcopy(db.base)
    model = result["model"]
    model["annotations"] = [{"name": a.Name, "value": a.Value} for a in db.Model.Annotations]
    model["tables"] = []
    for table in db.Model.Tables:
        t = deepcopy(table.base)
        if table.Measures:
            t["measures"] = [{**m.base, "expression": m.Expression} for m in table.Measures]
        for partition in t["partitions"]:
            partition["source"]["expressionSource"] = table.Partitions[partition["name"]].Source.ExpressionSource.Name
        model["tables"].append(t)
    model["roles"] = [{
        **role.base, "members": list(role.Members),
        "tablePermissions": [{"name": p.Table.Name, "filterExpression": p.FilterExpression}
                             for p in role.TablePermissions],
    } for role in db.Model.Roles]
    model["relationships"] = [{
        **r.base, "fromTable": r.FromTable.Name, "fromColumn": r.FromColumn.Name,
        "toTable": r.ToTable.Name, "toColumn": r.ToColumn.Name,
    } for r in db.Model.Relationships]
    return json.dumps(result)


def editor_for(bim):
    editor = TomEditor.__new__(TomEditor)
    editor._readonly = False
    editor._database = database(bim)
    editor._tom = Node(
        SerializeOptions=Node, Annotation=Node, TablePermission=Permission,
        JsonSerializer=Node(
            SerializeDatabase=serialize,
            DeserializeDatabase=lambda value: database(json.loads(value)),
        ),
    )
    return editor


@pytest.mark.parametrize("marker_only", [False, True])
def test_targeted_tom_upgrade_uses_one_save_preserves_existing_objects_and_wires_live_sources(marker_only):
    target = build_bim("Owner", "host", "database")
    existing = deepcopy(target) if marker_only else _legacy_contract(target)
    existing["model"]["annotations"] = [
        a for a in existing["model"]["annotations"] if a["name"] != "OwnerContractVersion"
    ]
    existing["model"]["roles"][0]["members"] = [{"memberName": "approved-reader"}]
    editor = editor_for(existing)
    model = editor._database.Model
    tables, roles, expressions = list(model.Tables), list(model.Roles), list(model.Expressions)
    members = roles[0].Members
    partitions = [p for t in tables for p in t.Partitions]
    migration = "restore-contract-2-marker" if marker_only else "legacy-five-table-to-2"
    editor.apply(target, migration)
    model.SaveChanges.assert_called_once_with()
    assert all(model.Tables[t.Name] is t for t in tables)
    assert all(model.Roles[r.Name] is r for r in roles)
    assert all(model.Expressions[e.Name] is e for e in expressions)
    assert roles[0].Members is members and members == [{"memberName": "approved-reader"}]
    assert all(any(p is current for t in model.Tables for current in t.Partitions) for p in partitions)
    for name in _NEW_TABLES:
        table = model.Tables[name]
        for p in table.Partitions:
            assert p.Source.ExpressionSource is model.Expressions[p.Source.ExpressionSource.Name]
        assert model.Roles["WorkspaceOwner"].TablePermissions[name].Table is table
    for relationship in model.Relationships:
        assert relationship.FromTable is model.Tables[relationship.FromTable.Name]
        assert relationship.ToTable is model.Tables[relationship.ToTable.Name]
    assert len(model.Roles["WorkspaceOwner"].TablePermissions) == 7


def test_bad_candidate_security_stops_before_save():
    target = build_bim("Owner", "host", "database")
    editor = editor_for(_legacy_contract(target))
    def deserialize(value):
        desired = database(json.loads(value))
        desired.Model.Roles["WorkspaceOwner"].TablePermissions["owner_executions"].FilterExpression = "TRUE()"
        return desired
    editor._tom.JsonSerializer.DeserializeDatabase = deserialize
    with pytest.raises(ValueError, match="pre-save security validation"):
        editor.apply(target, "legacy-five-table-to-2")
    editor._database.Model.SaveChanges.assert_not_called()


def test_missing_serialized_members_prevents_snapshot_or_update():
    target = build_bim("Owner", "host", "database")
    existing = _legacy_contract(target)
    existing["model"]["roles"][0]["members"] = [{"memberName": "reader"}]
    editor = editor_for(existing)
    def omit_members(db, options):
        result = json.loads(serialize(db, options))
        result["model"]["roles"][0].pop("members")
        return json.dumps(result)
    editor._tom.JsonSerializer.SerializeDatabase = omit_members
    with pytest.raises(ValueError, match="serialized owner role memberships"):
        editor.read()
    editor._database.Model.SaveChanges.assert_not_called()
