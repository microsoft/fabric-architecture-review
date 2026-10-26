# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Findings name the items they flag, so the Items to review page, the app and the owner report can list them."""
from analyzers import native_rules
from analyzers.rule_support import MAX_LISTED_ITEMS, listed
from tests import test_enrichment_rules as fuam
from tests import test_gap_signals as gap
from tests import test_native_rules as native

A, B, C = ("%08d-0000-4000-8000-000000000000" % n for n in (1, 2, 3))


def test_listed_ids_are_distinct_lowercase_sorted_and_capped():
    assert listed([B.upper(), A, A, None, "", C]) == [A, B, C]
    assert len(listed(f"{n:08d}-0000-4000-8000-000000000000" for n in range(MAX_LISTED_ITEMS + 50))) == MAX_LISTED_ITEMS


# --- governance and architecture signals from the inventory and the scan ----------------------

def test_documentation_and_naming_signals_name_the_items_that_miss_them():
    items = [{"id": A, "type": "Notebook", "name": "prod-load", "description": "Loads sales"},
             {"id": B, "type": "Lakehouse", "name": "scratch"},
             {"id": C, "type": "Report", "name": "Prod-Report", "description": ""}]
    ws = gap.hosted(items=items, itemsCollectionStatus="collected")
    assert gap.run("ARCH-009", ws, **{})[1]["undocumented_item_ids"] == [B, C]
    config = gap.configuration()
    config["thresholds"]["governance"]["naming_pattern"] = "prod-.*"
    evidence = gap.run("GOV-004", ws, config, **{})[1]
    assert evidence["misnamed_item_ids"] == [B, C]


def test_endorsement_and_labels_name_the_items_without_them_including_dataflows():
    ws = native.workspace(native.PROD)
    scan = native.scanner(native.PROD, datasets=[
        {"id": A, "endorsementDetails": {"endorsement": "Certified"}, "sensitivityLabel": {"labelId": "x"}},
        {"id": B, "endorsementDetails": {"endorsement": "None"}},
        {"id": C.upper()}], dataflows=[{"objectId": "dddddddd-0000-4000-8000-000000000004"}])
    ctx = native.context(**{"scanner.json": scan})
    assert native_rules.endorsement(ws, native.effective("GOV-005", ws), ctx)[1]["unendorsed_model_ids"] == [B, C]
    assert native_rules.label_coverage(ws, native.effective("SEC-012", ws), ctx)[1]["unlabelled_item_ids"] == [
        B, C, "dddddddd-0000-4000-8000-000000000004"]


def test_cross_workspace_and_production_dependencies_name_the_dependent_items():
    ws = native.workspace(native.PROD)
    scan = native.scanner(native.PROD, datasets=[
        {"id": A, "upstreamDatasets": [{"groupId": native.NONPROD}]},
        {"id": B, "upstreamDatasets": [{"groupId": native.OTHER}]},
        {"id": C, "upstreamDatasets": [{"groupId": native.PROD}]}])
    evidence = native_rules.cross_workspace(ws, native.effective("ARCH-014", ws), native.context(**{"scanner.json": scan}))[1]
    assert (evidence["dependent_item_count"], evidence["dependent_item_ids"]) == (2, [A, B])
    flagged = gap.run("REL-003", gap.hosted(), **gap.granted(datasets=[
        {"id": A, "upstreamDatasets": [{"groupId": gap.NONPROD}]}, {"id": B, "upstreamDatasets": [{"groupId": gap.UNDECLARED}]}]))[1]
    assert (flagged["dependent_item_count"], flagged["nonproduction_dependent_item_ids"]) == (2, [A])


def test_legacy_items_and_notebook_patterns_name_what_they_found():
    ws = {"id": native.PROD, **native.inventory((A, "Datamart"), (B, "Notebook"), (C, "Datamart"))}
    assert native_rules.legacy_items(ws)[1]["legacy_item_ids"] == [A, C]
    rows = [native.notebook_row(A, signals={"ARCH-011": 2}), native.notebook_row(B, signals={"ARCH-011": 0}),
            native.notebook_row(C, signals={"ARCH-012": 1})]
    notebooks = {"id": native.PROD, **native.inventory((A, "Notebook"), (B, "Notebook"), (C, "Notebook"))}
    ctx = native.context(**native.definitions(rows))
    evidence = native_rules.notebook_pattern("ARCH-011", notebooks, ctx)[1]
    assert (evidence["pattern_hit_count"], evidence["notebook_ids_with_patterns"]) == (2, [A])


# --- signals from FUAM ------------------------------------------------------------------------

def test_stale_items_name_the_oldest_items_the_source_listed():
    row = {"workspace_id": fuam.WS, "item_count": 6, "dated_item_count": 6, "stale_item_count": 4, "stale_item_ids": [B.upper(), A]}
    evidence = fuam.evaluate("GOV-008", {"item_age": fuam.entry([row])})[1]
    assert (evidence["stale_item_ids"], evidence["stale_item_count"]) == ([A, B], 4)
    older = {k: v for k, v in row.items() if k != "stale_item_ids"}
    assert fuam.evaluate("GOV-008", {"item_age": fuam.entry([older])})[1]["stale_item_ids"] == []


def test_failing_heavy_and_refresh_failing_items_are_named():
    ops = fuam.operation_facts(fuam.operation(A, 100, 30), fuam.operation(B, 100, 5), fuam.operation(C, 100, 50, wid=fuam.OTHER_WS))
    assert fuam.evaluate("OPS-002", ops)[1]["failing_item_ids"] == [A]
    heavy = fuam.expensive_facts([fuam.operation(A, 10, 0, cus=40.0), fuam.operation(B, 10, 0, cus=10.0),
                                  fuam.operation(C, 10, 0, cus=30.0)], [(fuam.CAP, 100.0)])
    assert fuam.evaluate("OPS-003", heavy)[1]["heavy_item_ids"] == [A, C]
    refreshes = fuam.refresh_facts((fuam.WS, A, 10, 9), (fuam.WS, B, 10, 0), (fuam.OTHER_WS, C, 10, 10))
    assert fuam.evaluate("OPS-005", refreshes)[1]["refresh_failing_item_ids"] == [A]
