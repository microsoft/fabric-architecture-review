# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Pure, allowlisted Dataflow Gen2 evidence; never evaluate Power Query M.

Integration: ``build_dataflow_evidence(raw_dir, run_id, run_timestamp)`` returns
``gold_dataflows`` and ``gold_dataflow_queries``. All fields are strings except
query_count, flagged_query_count and signal_count (integers / Delta int64).
Identity is (workspace_id, dataflow_id), never a display name. Empty dataflow_id
denotes an inventory coverage gap, not a discovered item.
query_count counts observed names (including metadata-only names when M is
missing); signal_count counts distinct codes, not call occurrences.

The collector persists only the result of ``inspect_definition`` in
dataflows.json, not M, literals, connections, or definition/error payloads.
The builder revalidates that projection rather than trusting raw free text.
Statuses: inspected, partial, parse_error, unavailable, forbidden, unsupported,
and inventory_unavailable. "inspected" means lexical static checks completed,
not that M compiles, folds, or performs well. MDF and unknown extra parts are
explicit analysis gaps. Legacy scanner dataflows are not a Gen2 inventory.

Supported documented layouts:
https://learn.microsoft.com/rest/api/fabric/articles/item-management/definitions/dataflow-definition
https://learn.microsoft.com/fabric/data-factory/dataflow-gen2-public-apis
"""
from __future__ import annotations

from collectors.workspace_scope import filter_review_payload

import base64
import binascii
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

SIGNALS = {
    "DFLOW_TABLE_BUFFER": "Review explicit buffering; measure impact before changing it.",
    "DFLOW_STOP_FOLDING": "Review the explicit folding boundary and its intended placement.",
    "DFLOW_NATIVE_QUERY": "Review the native query boundary; folding support is connector-specific.",
}
CALLS = {
    "Table.Buffer": "DFLOW_TABLE_BUFFER",
    "Table.StopFolding": "DFLOW_STOP_FOLDING",
    "Value.NativeQuery": "DFLOW_NATIVE_QUERY",
}
NOTICES = {
    "STATIC_ONLY": "Lexical M checks only; no full grammar validation, execution, folding validation, or runtime measurement.",
    "PARSE_GAP": "M structure could not be fully inspected; no clean conclusion is available.",
    "DEFINITION_UNAVAILABLE": "Definition unavailable; not an indication of a clean dataflow.",
    "PERMISSION_DENIED": "Definition requires read/write permission; access was denied.",
    "UNSUPPORTED_DEFINITION": "Definition variant or operation is unsupported; analysis unavailable.",
    "EXTRA_PARTS": "Additional definition parts (including possible MDF transforms) were not analyzed.",
    "QUERY_METADATA_GAP": "Query metadata and M declarations are incomplete or inconsistent.",
    "DUPLICATE_ID": "Duplicate identity is ambiguous; coverage is incomplete.",
    "INVENTORY_GAP": "Dataflow inventory is incomplete; undiscovered items may exist.",
    "MISSING_COLLECTION": "Native Dataflow inventory was not collected.",
    "REDACTED_NAME": "A name was redacted by the publication allowlist.",
}
STATUSES = frozenset({
    "inspected", "partial", "parse_error", "unavailable", "forbidden",
    "unsupported", "inventory_unavailable",
})
MAX_PART_BYTES = 8 * 1024 * 1024
_IDENT = re.compile(r"[^\W\d]\w*(?:\.[^\W\d]\w*)*", re.UNICODE)
_SAFE_NAME = re.compile(r"[A-Za-z0-9 _()\-]{1,128}\Z")
_SENSITIVE_NAME = re.compile(r"password|secret|token|credential|accountkey|bearer", re.I)
_SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")


def safe_name(value: Any) -> str:
    """Only publish simple names; never publish path/URL/connection-like names."""
    if isinstance(value, str) and _SAFE_NAME.fullmatch(value) and not _SENSITIVE_NAME.search(value):
        return value
    digest = hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:12]
    return f"redacted-{digest}"


def safe_id(value: Any) -> str:
    """Canonicalize Fabric IDs, excluding any URL/path/connection content."""
    return value.lower() if isinstance(value, str) and _SAFE_ID.fullmatch(value) else ""


def _notice(codes: list[str]) -> str:
    return " ".join(NOTICES[code] for code in sorted(set(codes)) if code in NOTICES)


def _decode(part: dict) -> str:
    payload = part.get("payload")
    if part.get("payloadType") != "InlineBase64" or not isinstance(payload, str):
        raise ValueError("unsupported part encoding")
    if len(payload) > MAX_PART_BYTES * 4 // 3 + 4:
        raise ValueError("part exceeds static analysis limit")
    return base64.b64decode(payload.strip(), validate=True).decode("utf-8-sig")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _tokens(source: str) -> tuple[list[tuple[str, str]], bool]:
    """Lex M, discarding comments and literal contents, retaining identifier names.

    This is deliberately not an M compiler. Quoted identifiers are distinct
    from ordinary identifiers, so a field named #"Table.Buffer" is not a call
    to the library function. Unsupported escapes produce a coverage gap.
    """
    tokens: list[tuple[str, str]] = []
    i = 0
    gap = False
    while i < len(source):
        if source[i].isspace():
            i += 1
        elif source.startswith("//", i):
            end = source.find("\n", i + 2)
            i = len(source) if end < 0 else end + 1
        elif source.startswith("/*", i):
            end = source.find("*/", i + 2)
            if end < 0:
                return tokens, True
            i = end + 2
        elif source[i] == '"' or source.startswith('#"', i):
            quoted = source[i] == "#"
            i += 2 if quoted else 1
            text: list[str] = []
            closed = False
            while i < len(source):
                if source.startswith('""', i):
                    if quoted:
                        text.append('"')
                    i += 2
                elif source[i] == '"':
                    i += 1
                    closed = True
                    break
                else:
                    if quoted:
                        text.append(source[i])
                    i += 1
            if not closed:
                return tokens, True
            name = "".join(text) if quoted else ""
            if quoted and "#(" in name:
                gap = True
            tokens.append(("quoted" if quoted else "literal", name))
        else:
            match = _IDENT.match(source, i)
            if match:
                tokens.append(("identifier", match.group()))
                i = match.end()
            else:
                # Literal numeric contents are never returned to callers.
                if source[i].isdigit():
                    end = i + 1
                    while end < len(source) and (source[end].isalnum() or source[end] == "."):
                        end += 1
                    tokens.append(("literal", ""))
                    i = end
                else:
                    tokens.append(("punctuation", source[i]))
                    i += 1
    return tokens, gap


def _segments(tokens: list[tuple[str, str]]) -> tuple[list[list[tuple[str, str]]], bool]:
    stack: list[str] = []
    segments: list[list[tuple[str, str]]] = []
    start = 0
    for i, (kind, token) in enumerate(tokens):
        if kind != "punctuation":
            continue
        if token in ("(", "[", "{"):
            stack.append(token)
        elif token in (")", "]", "}"):
            if not stack or stack.pop() != {")": "(", "]": "[", "}": "{"}[token]:
                return segments, True
        elif token == ";" and not stack:
            segments.append(tokens[start:i])
            start = i + 1
    return segments, bool(stack or start != len(tokens))


def _unannotated(tokens: list[tuple[str, str]]) -> list[tuple[str, str]]:
    if tokens and tokens[0] == ("punctuation", "["):
        depth = 0
        for i, (kind, value) in enumerate(tokens):
            if kind != "punctuation":
                continue
            depth += value == "["
            depth -= value == "]"
            if depth == 0:
                return tokens[i + 1:]
        return []
    return tokens


def _inspect_m(source: str) -> tuple[list[dict], bool]:
    tokens, gap = _tokens(source)
    segments, structure_gap = _segments(tokens)
    gap |= structure_gap
    if not segments:
        return [], True
    header = _unannotated(segments[0])
    if (len(header) != 2 or header[0] != ("identifier", "section")
            or header[1][0] not in ("identifier", "quoted")):
        return [], True
    queries: list[dict] = []
    names: set[str] = set()
    declarations = []
    for segment in segments[1:]:
        if segment and segment[0] == ("punctuation", "["):
            # ItemType annotations can designate non-M external transforms.
            # Even a missing external part must not become clean coverage.
            annotation = segment[:len(segment) - len(_unannotated(segment))]
            gap |= any(token == ("identifier", "ItemType") for token in annotation)
        segment = _unannotated(segment)
        if segment and segment[0] == ("identifier", "shared"):
            segment = segment[1:]
        if (len(segment) < 3 or segment[0][0] not in ("identifier", "quoted")
                or segment[1][1] != "=" or not segment[2:]):
            gap = True
            continue
        declarations.append(segment)
    section_shadows = {segment[0][1] for segment in declarations} & CALLS.keys()
    for segment in declarations:
        name = segment[0][1]
        body = segment[2:]
        local_gap = False
        # Bindings/parameters can shadow standard-library names. Report a gap
        # instead of claiming such calls execute a built-in function.
        shadowed = section_shadows | {value for i, (kind, value) in enumerate(body[:-1])
                    if kind in ("identifier", "quoted") and value in CALLS
                    and body[i + 1][1] in ("=", "as", ",", ")")}
        local_gap |= bool(shadowed)
        local_gap |= any(
            (kind == "punctuation" and value == "=" and
             (body[i + 1][1] in ("in", "then", "else", ",", ")")))
            for i, (kind, value) in enumerate(body[:-1])
        )
        local_gap |= body[-1][1] in ("=", ",", "let", "in", "then", "else")
        local_gap |= sum(token == ("identifier", "let") for token in body) != sum(
            token == ("identifier", "in") for token in body)
        codes: set[str] = set()
        for i, (kind, value) in enumerate(body[:-1]):
            if (kind == "identifier" and value in CALLS and value not in shadowed
                    and body[i + 1][1] == "("
                    and (i == 0 or body[i - 1][1] not in ("[", "!", "@"))):
                codes.add(CALLS[value])
        if name in names:
            gap = True
            continue
        names.add(name)
        # Dynamic M evaluation cannot be inspected from literal-free tokens.
        local_gap |= any(value == "Expression.Evaluate" for _, value in body)
        queries.append({"name": name, "signalCodes": sorted(codes),
                        "noticeCodes": ["PARSE_GAP"] if local_gap else ["STATIC_ONLY"]})
        gap |= local_gap
    return queries, gap


def inspect_definition(payload: Any) -> dict:
    """Decode documented CI/CD variants in memory; return only safe evidence."""
    result: dict = {"definitionStatus": "unsupported", "queries": [],
                    "noticeCodes": ["UNSUPPORTED_DEFINITION"]}
    if not isinstance(payload, dict) or not isinstance(payload.get("definition"), dict):
        return result
    parts = payload["definition"].get("parts")
    if not isinstance(parts, list) or not parts:
        return result
    if not all(isinstance(part, dict) and isinstance(part.get("path"), str) for part in parts):
        return {**result, "definitionStatus": "parse_error", "noticeCodes": ["PARSE_GAP"]}
    paths = [part["path"] for part in parts]
    if len(paths) != len(set(paths)):
        return {**result, "definitionStatus": "parse_error", "noticeCodes": ["DUPLICATE_ID"]}
    by_path = {part["path"]: part for part in parts}
    metadata: Any = None
    known = {".platform"}
    try:
        if "mashup.pq" in by_path and "queryMetadata.json" in by_path:
            known |= {"mashup.pq", "queryMetadata.json"}
            metadata = json.loads(_decode(by_path["queryMetadata.json"]), object_pairs_hook=_unique_object)
            if not isinstance(metadata, dict) or metadata.get("formatVersion") != "202502":
                return result
            source = _decode(by_path["mashup.pq"])
        elif "dataflow-content.json" in by_path:
            known.add("dataflow-content.json")
            document = json.loads(_decode(by_path["dataflow-content.json"]), object_pairs_hook=_unique_object)
            if not isinstance(document, dict):
                return result
            metadata = document.get("editingSessionMashup")
            if not isinstance(metadata, dict) or not isinstance(metadata.get("mashupDocument"), str):
                return result
            source = metadata["mashupDocument"]
        else:
            return result
    except (ValueError, binascii.Error, UnicodeDecodeError):
        return {**result, "definitionStatus": "parse_error", "noticeCodes": ["PARSE_GAP"]}
    queries, gap = _inspect_m(source)
    notices = ["STATIC_ONLY"]
    if gap:
        notices.append("PARSE_GAP")
    if set(paths) - known:
        notices.append("EXTRA_PARTS")
    query_metadata = metadata.get("queriesMetadata")
    if query_metadata is not None:
        if not isinstance(query_metadata, dict):
            notices.append("QUERY_METADATA_GAP")
        else:
            meta_names = []
            query_ids = []
            for entry in query_metadata.values():
                if not isinstance(entry, dict) or not isinstance(entry.get("queryName"), str):
                    notices.append("QUERY_METADATA_GAP")
                    continue
                meta_names.append(entry["queryName"])
                query_ids.append(entry.get("queryId"))
            if (set(meta_names) != {q["name"] for q in queries}
                    or len(meta_names) != len(set(meta_names))
                    or any(not isinstance(qid, str) for qid in query_ids)
                    or len(query_ids) != len(set(str(qid) for qid in query_ids))):
                notices.append("QUERY_METADATA_GAP")
            for name in sorted(set(meta_names) - {q["name"] for q in queries}):
                queries.append({"name": name, "signalCodes": [], "noticeCodes": ["PARSE_GAP"]})
    for query in queries:
        name = safe_name(query["name"])
        if name != query["name"]:
            query["noticeCodes"].append("REDACTED_NAME")
        query["name"] = name
        if len(notices) > 1:
            query["noticeCodes"] = sorted(set(query["noticeCodes"] + notices))
    status = "inspected" if len(notices) == 1 else "partial" if queries else "parse_error"
    return {"definitionStatus": status, "queries": queries, "noticeCodes": sorted(set(notices))}


def _read_collection(raw_dir: Path) -> dict:
    path = raw_dir / "dataflows.json"
    if not path.exists():
        return {"collectionComplete": False, "noticeCode": "MISSING_COLLECTION"}
    try:
        payload = filter_review_payload(
            json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=_unique_object), raw_dir,
        )
    except (OSError, ValueError):
        return {"collectionComplete": False, "noticeCode": "INVENTORY_GAP"}
    if (not isinstance(payload, dict) or payload.get("schemaVersion") != 1
            or not isinstance(payload.get("dataflows"), list)
            or not isinstance(payload.get("workspaces"), list)):
        return {"collectionComplete": False, "noticeCode": "INVENTORY_GAP"}
    return payload


def build_dataflow_evidence(raw_dir: Path, run_id: str, run_timestamp: str) -> dict[str, list[dict]]:
    """Build source-free tables from the native collector's safe projection."""
    payload = _read_collection(raw_dir)
    flows: list[dict] = []
    queries: list[dict] = []
    common = {"run_id": run_id, "run_timestamp": run_timestamp}
    records = payload.get("dataflows", [])
    identities = Counter((safe_id(item.get("workspaceId")), safe_id(item.get("id")))
                         for item in records if isinstance(item, dict))
    seen: set[tuple[str, str]] = set()
    malformed = False
    for item in records:
        if not isinstance(item, dict):
            malformed = True
            continue
        key = (safe_id(item.get("workspaceId")), safe_id(item.get("id")))
        if not all(key):
            malformed = True
            continue
        if key in seen:
            continue
        seen.add(key)
        base = {**common, "workspace_id": key[0], "workspace_name": safe_name(item.get("workspaceName", "")),
                "dataflow_id": key[1], "dataflow_name": safe_name(item.get("displayName", ""))}
        status = item.get("definitionStatus")
        status = status if isinstance(status, str) and status in STATUSES else "unavailable"
        codes = item.get("noticeCodes")
        codes = [code for code in codes if isinstance(code, str) and code in NOTICES] if isinstance(codes, list) else []
        rows = item.get("queries")
        if not isinstance(rows, list):
            rows = []
            status = "parse_error" if status == "inspected" else status
        if identities[key] > 1:
            status, codes, rows = "partial", ["DUPLICATE_ID"], []
        if status not in ("inspected", "partial", "parse_error"):
            rows = []
            codes.append({"forbidden": "PERMISSION_DENIED", "unsupported": "UNSUPPORTED_DEFINITION"}
                         .get(status, "DEFINITION_UNAVAILABLE"))
        item_queries: list[dict] = []
        query_names: set[str] = set()
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("name"), str):
                status = "partial"
                codes.append("PARSE_GAP")
                continue
            name = safe_name(row["name"])
            if name in query_names:
                status = "partial"
                codes.append("DUPLICATE_ID")
                continue
            query_names.add(name)
            signals = row.get("signalCodes")
            query_notices = row.get("noticeCodes")
            if (not isinstance(signals, list) or not isinstance(query_notices, list)
                    or any(not isinstance(code, str) or code not in SIGNALS for code in signals)
                    or any(not isinstance(code, str) or code not in NOTICES for code in query_notices)):
                status = "partial"
                codes.append("PARSE_GAP")
            signals = sorted({code for code in signals if isinstance(code, str) and code in SIGNALS}) if isinstance(signals, list) else []
            query_notices = [code for code in query_notices if isinstance(code, str) and code in NOTICES] if isinstance(query_notices, list) else ["PARSE_GAP"]
            if "PARSE_GAP" in query_notices and status == "inspected":
                status = "partial"
            item_queries.append({**base, "query_name": name, "signal_codes": ";".join(signals),
                                 "signal_count": len(signals),
                                 "recommendation": " ".join(SIGNALS[code] for code in signals) or
                                 "No selected static signals observed; validate runtime behavior separately.",
                                 "notice": _notice(query_notices + ["STATIC_ONLY"])})
        if status in ("partial", "parse_error"):
            codes.append("PARSE_GAP")
        for row in item_queries:
            row["notice"] = _notice(codes + ["STATIC_ONLY"]) + " " + row["notice"]
            if status != "inspected" and not row["signal_count"]:
                row["recommendation"] = "Complete definition analysis before drawing a clean conclusion."
        queries.extend(item_queries)
        flows.append({**base, "definition_status": status, "query_count": len(item_queries),
                      "flagged_query_count": sum(row["signal_count"] > 0 for row in item_queries),
                      "notice": _notice(codes + ["STATIC_ONLY"])})
    gaps: dict[str, str] = {}
    for workspace in payload.get("workspaces", []):
        if not isinstance(workspace, dict):
            malformed = True
        elif workspace.get("inventoryStatus") != "available":
            gaps[safe_id(workspace.get("id"))] = safe_name(workspace.get("name", ""))
    if (payload.get("collectionComplete") is not True and not gaps) or malformed:
        gaps[""] = ""
    for workspace_id, name in sorted(gaps.items()):
        notice_code = "MISSING_COLLECTION" if payload.get("noticeCode") == "MISSING_COLLECTION" else "INVENTORY_GAP"
        flows.append({**common, "workspace_id": workspace_id, "workspace_name": name,
                      "dataflow_id": "", "dataflow_name": "", "definition_status": "inventory_unavailable",
                      "query_count": 0, "flagged_query_count": 0, "notice": _notice([notice_code])})
    return {"gold_dataflows": flows, "gold_dataflow_queries": queries}
