# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from reports.owner.upgrade_tom import TomEditor
from reports.owner.model import build_bim


class _Permission:
    """A TOM table permission backed by one entry of a model dictionary."""

    def __init__(self, entry):
        self._entry, self.Name = entry, entry["name"]

    FilterExpression = property(lambda self: self._entry["filterExpression"],
                                lambda self, value: self._entry.__setitem__("filterExpression", value))


class _Measure:
    def __init__(self, entry):
        self._entry = entry

    Expression = property(lambda self: self._entry["expression"],
                          lambda self, value: self._entry.__setitem__("expression", value))


def test_rebinding_rewrites_only_the_pinned_filters_and_measures_and_saves_after_verification():
    from copy import deepcopy
    from reports.owner.deployment import _security_shape
    from tests.test_owner_upgrade import rebound
    target = build_bim("owner", "server", "db")
    stored = rebound(target)
    stored["model"]["roles"][0]["members"] = [{"memberName": "approved@example.com"}]
    members = deepcopy(stored["model"]["roles"][0]["members"])
    role = SimpleNamespace(Name="WorkspaceOwner",
                           TablePermissions=[_Permission(p) for p in stored["model"]["roles"][0]["tablePermissions"]])
    tables = {t["name"]: SimpleNamespace(Measures={m["name"]: _Measure(m) for m in t.get("measures", [])})
              for t in stored["model"]["tables"]}
    save = Mock()
    editor = TomEditor.__new__(TomEditor)
    editor._readonly = False
    editor._database = SimpleNamespace(Model=SimpleNamespace(Roles=[role], Tables=tables, SaveChanges=save))
    editor.read = editor._serialize = lambda: deepcopy(stored)

    editor.apply(target, "refresh-profile-binding")

    assert _security_shape(stored) == _security_shape(target)
    assert stored["model"]["roles"][0]["members"] == members
    save.assert_called_once()


def test_readonly_and_legacy_tom_migrations_cannot_apply_or_save():
    editor = TomEditor.__new__(TomEditor)
    editor._readonly = True
    editor._database = SimpleNamespace(Model=SimpleNamespace(SaveChanges=Mock()))
    with pytest.raises(ValueError, match="Read-only"):
        editor.apply(build_bim("owner", "server", "db"), "restore-contract-3-marker")
    editor._readonly = False
    with pytest.raises(ValueError, match="Unsupported"):
        editor.apply(build_bim("owner", "server", "db"), "legacy-five-table-to-2")
    editor._database.Model.SaveChanges.assert_not_called()
