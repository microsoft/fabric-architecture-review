# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Source-pattern signals and the sanitized activity policy extracted from definitions."""
import base64
import json
import re

import pytest

from analyzers import native_rules
from analyzers.rule_support import Context
from collectors.definition_safety import SIGNAL_PATTERNS, notebook_summary, pipeline_summary

GUID = "123e4567-e89b-12d3-a456-426614174000"
OTHER_GUID = "223e4567-e89b-12d3-a456-426614174001"
WS = "11111111-1111-1111-1111-111111111111"
PIPELINE = "dddddddd-dddd-dddd-dddd-dddddddddddd"
NOTEBOOK = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"


def hits(rule_id, source):
    return len(re.findall(SIGNAL_PATTERNS[rule_id], source))


def part(path, text):
    return {"path": path, "payloadType": "InlineBase64", "payload": base64.b64encode(text.encode()).decode()}


def definition(*parts):
    return {"definition": {"parts": list(parts)}}


def notebook(*cells, tags=None):
    """An ipynb definition; cells are source strings, or (source, tags) pairs."""
    body = []
    for cell in cells:
        source, cell_tags = cell if isinstance(cell, tuple) else (cell, [])
        body.append({"cell_type": "code", "source": source, "metadata": {"tags": cell_tags}})
    return definition(part("notebook-content.ipynb", json.dumps({"cells": body})))


# --- ARCH-011 ----------------------------------------------------------------------------------

@pytest.mark.parametrize("source", [
    'df = spark.read.load("abfss://container@account.dfs.core.windows.net/path/data")',
    'df = spark.read.load("abfs://container@account.dfs.core.windows.net/path")',
    'ABFSS://Container@account.dfs.core.windows.net/x',
    'url = "jdbc:sqlserver://server.database.windows.net:1433;database=db"',
    'JDBC:postgresql://host/db',
    'conn = "Server=x.database.windows.net;Database=db"',
    'conn = "Data Source=abc.fabric.microsoft.com"',
    'conn = "server = tenant-1.database.windows.net"',
    f'workspace_id = "{GUID}"',
    f"lakehouse_id = '{GUID}'",
    f'{{"warehouse_id": "{GUID}"}}',
    f'workspaceId: "{GUID}"',
    f'dataset id = "{GUID}"',
    f'CAPACITY_ID="{GUID.upper()}"',
])
def test_environment_bound_literals_count(source):
    assert hits("ARCH-011", source) == 1


def test_each_literal_is_counted_once():
    source = "\n".join([
        'a = "abfss://c@a.dfs.core.windows.net/x"',
        'b = "jdbc:sqlserver://s"',
        'c = "Server=x.database.windows.net"',
        f'workspace_id = "{GUID}"',
        f'lakehouse_id = "{OTHER_GUID}"',
    ])
    assert hits("ARCH-011", source) == 5


@pytest.mark.parametrize("source", [
    f'label = "{GUID}"',
    f"ids = ['{GUID}', '{OTHER_GUID}']",
    f"# {GUID}",
    f'item_id = "{GUID}"',
    f'other_workspace = "{GUID}"',
    'workspace_id = workspace_id_param',
    'workspace_id = notebookutils.runtime.context["currentWorkspaceId"]',
    'workspace_id = ""',
    'workspace_id = "not-a-guid"',
    f'workspace_id = {GUID}',  # unquoted
    'path = "Files/raw/data.csv"',
    'path = base_path + "/Tables/sales"',
    'path = f"{root}/Tables/sales"',
    'server = server_name',
    'conn = f"Server={server};Database=db"',
    'Server=localhost',
    'jdbc = "jdbc"',
    'url = "https://example.com/abfss"',
    'spark.sql("SELECT * FROM lakehouse.table")',
])
def test_parameterized_or_unrelated_values_do_not_count(source):
    assert hits("ARCH-011", source) == 0


# --- ARCH-012 ----------------------------------------------------------------------------------

@pytest.mark.parametrize("source", [
    "%pip install pandas",
    "!pip install pandas",
    "   %pip install pandas>=1.0",
    "%pip install pandas scikit-learn",
    "%PIP INSTALL pandas",
    "%pip  install   pandas",
    "!pip install -q pandas",
    "%pip install git+https://example.com/repo.git",
])
def test_unpinned_installs_count(source):
    assert hits("ARCH-012", source) == 1


@pytest.mark.parametrize("source", [
    "%pip install pandas==1.5.3",
    "%pip install pkg==1.2",
    "!pip install pkg==1.2",
    "%pip install -r requirements.txt",
    "%pip install  -r  requirements.txt",
    "%pip install --requirement requirements.txt",
    "%pip install pandas==1.5.3 numpy==1.26",
    "pip install pandas",  # not a notebook install command
    "# %pip install pandas",
    "print('%pip install pandas')",
    "%pip list",
    "%pip installed",
    "",
])
def test_pinned_or_requirement_installs_and_other_lines_do_not_count(source):
    assert hits("ARCH-012", source) == 0


def test_each_unpinned_line_counts_once_in_a_multiline_cell():
    source = "\n".join([
        "%pip install pandas",
        "import pandas",
        "%pip install -r requirements.txt",
        "!pip install requests",
        "%pip install numpy==1.26",
        "",
        "   %pip install scipy",
    ])
    assert hits("ARCH-012", source) == 3


# --- notebook_summary --------------------------------------------------------------------------

def test_notebook_summary_reports_counts_under_signals():
    summary = notebook_summary(notebook(
        'df = spark.read.load("abfss://c@a.dfs.core.windows.net/x")\n%pip install pandas',
        ['%pip install -r requirements.txt\n', f'workspace_id = "{GUID}"\n', "!pip install x\n"],
        ("workspace = ''\n", ["parameters"]),
    ))
    assert set(summary["signals"]) == {"ARCH-010", "ARCH-011", "ARCH-012", "SEC-011"}
    assert summary["signals"] == {"ARCH-010": 0, "ARCH-011": 2, "ARCH-012": 2, "SEC-011": 0}
    assert summary["signalCoverage"] == "complete"
    assert summary["parameterNames"] == ["workspace"]
    assert summary["metadataOnly"] is True


def test_notebook_summary_ignores_markdown_cells_and_keeps_only_counts():
    cells = [
        {"cell_type": "markdown", "source": "%pip install pandas"},
        {"cell_type": "code", "source": f'abfss://c@a.dfs.core.windows.net/x ; "{GUID}"', "metadata": {}},
        {"cell_type": "code", "source": "%pip install secretpackage", "metadata": {}},
    ]
    summary = notebook_summary(definition(part("nb.ipynb", json.dumps({"cells": cells}))))
    assert (summary["signals"]["ARCH-011"], summary["signals"]["ARCH-012"]) == (1, 1)
    serialized = json.dumps(summary)
    for leaked in ("abfss", "secretpackage", GUID, "dfs.core"):
        assert leaked not in serialized
    assert all(isinstance(value, int) for value in summary["signals"].values())


FABRIC_PY = (
    "# Fabric notebook source\n\n# METADATA ********************\n\n# META {\n# META   \"kernel_info\": {\"name\": \"synapse_pyspark\"}\n# META }\n\n"
    "# MARKDOWN ********************\n\n# %pip install ignored-in-markdown\n\n"
    "# PARAMETERS CELL ********************\n\nenvironment = 'dev'\nrun_size: int = 3\n\n"
    "# METADATA ********************\n\n# META {\n# META   \"language\": \"python\"\n# META }\n\n"
    "# CELL ********************\n\n%pip install pandas\nserver = 'Server=x.database.windows.net'\nlater = 1\n\n"
    "# METADATA ********************\n\n# META {\n# META   \"language\": \"python\"\n# META }\n"
)


def test_notebook_summary_reads_the_fabric_py_format():
    summary = notebook_summary(definition(part("notebook-content.py", FABRIC_PY)))
    assert summary["parameterNames"] == ["environment", "run_size"]
    assert summary["parameterCoverage"] == "complete"
    assert summary["signalCoverage"] == "complete"
    assert (summary["signals"]["ARCH-011"], summary["signals"]["ARCH-012"]) == (1, 1)


def test_py_notebook_without_a_parameter_cell_declares_no_parameters():
    text = "# CELL ********************\n\nx = 1\n\n# METADATA ********************\n\n# META {}\n"
    summary = notebook_summary(definition(part("notebook-content.py", text)))
    assert (summary["parameterNames"], summary["parameterCoverage"], summary["signalCoverage"]) == ([], "complete", "complete")


def test_py_notebook_parameter_cell_that_does_not_parse_is_partial_but_signals_stay_complete():
    text = "# PARAMETERS CELL ********************\n\n%%sql\nselect 1\n\n# CELL ********************\n\n%pip install pandas\n"
    summary = notebook_summary(definition(part("notebook-content.py", text)))
    assert summary["parameterCoverage"] == "partial"
    assert summary["signalCoverage"] == "complete"
    assert summary["signals"]["ARCH-012"] == 1


def test_py_notebook_magic_prefixed_lines_are_scanned():
    text = "# CELL ********************\n\n# MAGIC %pip install pandas\n"
    summary = notebook_summary(definition(part("notebook-content.py", text)))
    assert summary["signals"]["ARCH-012"] == 1


def test_non_python_notebook_source_is_scanned_but_parameters_stay_partial():
    summary = notebook_summary(definition(part("notebook-content.sql", "select 1")))
    assert (summary["parameterCoverage"], summary["signalCoverage"]) == ("partial", "complete")


@pytest.mark.parametrize("source", [None, {"x": 1}, 5])
def test_notebook_summary_marks_unreadable_cells_as_partial(source):
    cells = [{"cell_type": "code", "source": source, "metadata": {}},
             {"cell_type": "code", "source": "%pip install pandas", "metadata": {}}]
    summary = notebook_summary(definition(part("nb.ipynb", json.dumps({"cells": cells}))))
    assert summary["signalCoverage"] == "partial"
    assert summary["signals"]["ARCH-012"] == 1


@pytest.mark.parametrize("value", [None, {}, definition(), definition(part("nb.ipynb", "not json")),
                                   definition(part("nb.ipynb", json.dumps({"cells": "x"})))])
def test_notebook_summary_without_readable_source_has_unknown_coverage_and_zero_signals(value):
    summary = notebook_summary(value)
    assert summary["signalCoverage"] == "unknown"
    assert set(summary["signals"].values()) == {0}


# --- pipeline_summary --------------------------------------------------------------------------

def pipeline(*activities):
    body = {"properties": {"activities": list(activities)}}
    return definition(part("pipeline-content.json", json.dumps(body)))


def activities_of(summary):
    return summary["parts"][0]["decoded"]["properties"]["activities"]


def policy_of(policy, **extra):
    activity = {"name": "act", "type": "Copy", "policy": policy, **extra}
    return activities_of(pipeline_summary(pipeline(activity)))[0]


def test_integer_retry_and_a_wellformed_timeout_are_kept():
    for timeout in ("0.12:00:00", "1.00:00:00", "12:00:00", "7.00:30:15", "00:10:00"):
        assert policy_of({"retry": 3, "timeout": timeout})["policy"] == {"retry": 3, "timeout": timeout}
    assert policy_of({"retry": 0})["policy"] == {"retry": 0}
    assert policy_of({"timeout": "0.12:00:00"})["policy"] == {"timeout": "0.12:00:00"}


@pytest.mark.parametrize("policy", [
    {"retry": "@pipeline().parameters.retries"},
    {"retry": "3"},
    {"retry": True},
    {"retry": 2.5},
    {"retry": None},
    {"retry": {"value": "@pipeline().parameters.retries", "type": "Expression"}},
    {"timeout": "@pipeline().parameters.timeout"},
    {"timeout": {"value": "@pipeline().parameters.timeout", "type": "Expression"}},
    {"timeout": "twelve hours"},
    {"timeout": "0.12:00:00; DROP TABLE x"},
    {"timeout": "0.12:00"},
    {"timeout": "123:00:00"},
    {"timeout": 3600},
    {"timeout": None},
])
def test_free_text_and_expression_values_are_dropped_and_flagged_dynamic(policy):
    safe = policy_of(policy)["policy"]
    assert safe == {"dynamic": True}


def test_a_valid_value_next_to_a_dynamic_one_is_kept_and_the_policy_is_flagged():
    assert policy_of({"retry": 2, "timeout": "@variables('t')"})["policy"] == {"retry": 2, "dynamic": True}
    assert policy_of({"retry": "@variables('r')", "timeout": "0.01:00:00"})["policy"] == {
        "timeout": "0.01:00:00", "dynamic": True}


def test_nothing_else_from_the_policy_leaks():
    secret = "SECRET-VALUE"
    safe = policy_of({
        "retry": 1, "timeout": "0.01:00:00", "retryIntervalInSeconds": 30, "secureInput": True,
        "secureOutput": True, "connectionString": secret, "password": secret, "custom": {"nested": secret},
    })
    assert safe["policy"] == {"retry": 1, "timeout": "0.01:00:00"}
    assert secret not in json.dumps(safe)
    assert "dynamic" not in safe["policy"]


def test_a_policy_with_no_retry_or_timeout_stays_empty_and_not_dynamic():
    assert policy_of({})["policy"] == {}
    assert policy_of({"secureInput": True})["policy"] == {}


def test_an_absent_or_malformed_policy_adds_no_policy():
    activity = {"name": "act", "type": "Copy"}
    assert "policy" not in activities_of(pipeline_summary(pipeline(activity)))[0]
    for malformed in ("retry=3", ["retry"], 3, None):
        assert "policy" not in activities_of(pipeline_summary(pipeline({**activity, "policy": malformed})))[0]


def test_policy_is_kept_on_nested_activities():
    inner = {"name": "inner", "type": "Copy", "policy": {"retry": 2, "timeout": "0.01:00:00"}}
    outer = {"name": "loop", "type": "ForEach", "typeProperties": {"activities": [inner]}}
    nested = activities_of(pipeline_summary(pipeline(outer)))[0]["typeProperties"]["activities"][0]
    assert nested["policy"] == {"retry": 2, "timeout": "0.01:00:00"}


def test_the_sanitized_policy_feeds_the_resilience_rule():
    summary = pipeline_summary(pipeline(
        {"name": "ok", "type": "Copy", "policy": {"retry": 2, "timeout": "0.01:00:00"}},
        {"name": "none", "type": "Copy", "policy": {"retry": 0, "timeout": "0.12:00:00"}},
        {"name": "dynamic", "type": "Lookup", "policy": {"retry": "@pipeline().parameters.r"}},
        {"name": "absent", "type": "TridentNotebook"},
        {"name": "wait", "type": "Wait"},
    ))
    row = {"id": PIPELINE, "workspaceId": WS, "parts": summary["parts"]}
    ws = {"id": WS, "itemsCollectionStatus": "collected", "items": [{"id": PIPELINE, "type": "DataPipeline"}]}
    ctx = Context({"configuration": {}}, [ws], {"pipeline_definitions.json": {"pipelines": [row]}})
    status, evidence = native_rules.pipeline_resilience(ws, ctx)
    assert status == "info"
    assert evidence["executing_activity_count"] == 4
    assert evidence["without_retry_count"] == 2  # the zero retry and the absent policy
    assert evidence["default_timeout_count"] == 2
    assert evidence["review_recommended"] is True


def test_pipeline_summary_keeps_only_the_structure():
    summary = pipeline_summary(pipeline({
        "name": "act", "type": "Copy", "policy": {"retry": 1},
        "typeProperties": {"source": {"query": "SELECT secret FROM t"}, "notebookId": NOTEBOOK},
    }))
    assert summary["definitionCoverage"] == "complete" and summary["metadataOnly"] is True
    assert "SELECT" not in json.dumps(summary)
    assert activities_of(summary)[0]["typeProperties"] == {"notebookId": NOTEBOOK}
