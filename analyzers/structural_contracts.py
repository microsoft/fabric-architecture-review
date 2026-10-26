# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Static architectural contracts evaluated from sanitized structural evidence."""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Set, Tuple


_NESTED_ACTIVITY_KEYS = ("activities", "ifTrueActivities", "ifFalseActivities", "defaultActivities")

def _walk_activities(activities: Iterable[Dict[str, Any]]) -> Iterable[Dict[str, Any]]:
    """Yield every activity in a pipeline tree, descending into nested containers."""
    for a in activities or []:
        if not isinstance(a, dict):
            continue
        yield a
        tp = a.get("typeProperties") or {}
        for key in _NESTED_ACTIVITY_KEYS:
            nested = tp.get(key)
            if isinstance(nested, list):
                yield from _walk_activities(nested)
        # Switch / cases: list of {value, activities}
        for case in (tp.get("cases") or []):
            if isinstance(case, dict):
                yield from _walk_activities(case.get("activities") or [])

def _pipeline_json_from_parts(parts: List[Dict[str, Any]]) -> Dict[str, Any] | None:
    """Locate the pipeline-content JSON part inside a getDefinition result."""
    for part in parts or []:
        path = (part.get("path") or "").lower()
        decoded = part.get("decoded")
        if isinstance(decoded, dict) and (
            path.endswith("pipeline-content.json") or path.endswith(".json")
        ):
            # Pipeline content always carries either "activities" or "properties.activities".
            if "activities" in decoded or "properties" in decoded:
                return decoded
    return None

def _dependency_graph(content: Dict[str, Any]) -> Dict[str, Any]:
    """Validate literal edges within each activity array; never evaluate expressions."""
    reasons: Set[str] = set()
    properties = content.get("properties")
    activities = (content["activities"] if "activities" in content else
                  properties.get("activities") if isinstance(properties, dict) else None)
    tree: List[Dict[str, Any]] = []
    pending = [(activities, tree)]
    while pending:
        source, target = pending.pop()
        if not isinstance(source, list):
            reasons.add("activity_scope_unresolved")
            continue
        for activity in source:
            if not isinstance(activity, dict):
                reasons.add("activity_scope_unresolved")
                target.append({})
                continue
            node = {"name": activity.get("name"), "dependsOn": activity.get("dependsOn", [])}
            target.append(node)
            tp = activity.get("typeProperties", {})
            if not isinstance(tp, dict):
                reasons.add("activity_scope_unresolved")
                continue
            children: Dict[str, Any] = {}
            node["typeProperties"] = children
            kind = str(activity.get("type") or "").lower()
            if ((kind in {"foreach", "until"} and "activities" not in tp)
                    or (kind == "ifcondition" and not any(
                        key in tp for key in ("ifTrueActivities", "ifFalseActivities")))
                    or (kind == "switch" and not any(
                        key in tp for key in ("cases", "defaultActivities")))):
                reasons.add("activity_scope_unresolved")
            for key in _NESTED_ACTIVITY_KEYS:
                if key in tp:
                    children[key] = []
                    pending.append((tp[key], children[key]))
            if "cases" in tp:
                children["cases"] = []
                if not isinstance(tp["cases"], list):
                    reasons.add("activity_scope_unresolved")
                    continue
                for case in tp["cases"]:
                    branch: List[Dict[str, Any]] = []
                    children["cases"].append({"activities": branch})
                    pending.append((case.get("activities") if isinstance(case, dict) else None, branch))

    walked = list(_walk_activities(tree))
    scopes = [tree]
    for activity in walked:
        tp = activity.get("typeProperties") or {}
        scopes.extend(tp[key] for key in _NESTED_ACTIVITY_KEYS if key in tp)
        scopes.extend(case["activities"] for case in tp.get("cases", []))

    affected: Set[Tuple[int, int]] = set()
    signals: Set[str] = set()
    unresolved = 0
    for scope_index, scope in enumerate(scopes):
        names: Dict[str, List[int]] = {}
        for index, activity in enumerate(scope):
            name = activity.get("name")
            if isinstance(name, str) and name.strip() and not name.lstrip().startswith("@"):
                names.setdefault(name, []).append(index)
            else:
                reasons.add("activity_name_unresolved")
        names_complete = sum(len(indices) for indices in names.values()) == len(scope)
        for indices in names.values():
            if len(indices) > 1:
                signals.add("duplicate_activity")
                affected.update((scope_index, index) for index in indices)
        edges: Dict[int, Set[int]] = {index: set() for index in range(len(scope))}
        for index, activity in enumerate(scope):
            dependencies = activity.get("dependsOn", [])
            if not isinstance(dependencies, list):
                unresolved += 1
                continue
            for dependency in dependencies:
                ref = dependency.get("activity") if isinstance(dependency, dict) else None
                if not isinstance(ref, str) or not ref.strip() or ref.lstrip().startswith("@"):
                    unresolved += 1
                    continue
                targets = names.get(ref, [])
                if not targets:
                    if names_complete:
                        signals.add("missing_dependency")
                        affected.add((scope_index, index))
                    else:
                        unresolved += 1
                elif len(targets) > 1:
                    unresolved += 1
                elif targets[0] == index:
                    signals.add("self_dependency")
                    affected.add((scope_index, index))
                else:
                    edges[index].add(targets[0])

        # Iterative strongly connected components exclude downstream non-cycle nodes.
        seen: Set[int] = set()
        order: List[int] = []
        reverse: Dict[int, Set[int]] = {index: set() for index in edges}
        for source_index, targets in edges.items():
            for target_index in targets:
                reverse[target_index].add(source_index)
        for start in edges:
            stack = [(start, False)]
            while stack:
                current, finished = stack.pop()
                if finished:
                    order.append(current)
                elif current not in seen:
                    seen.add(current)
                    stack.append((current, True))
                    stack.extend((target_index, False) for target_index in sorted(edges[current], reverse=True))
        seen.clear()
        for start in reversed(order):
            if start in seen:
                continue
            component: Set[int] = set()
            remaining = [start]
            seen.add(start)
            while remaining:
                current = remaining.pop()
                component.add(current)
                for target_index in reverse[current] - seen:
                    seen.add(target_index)
                    remaining.append(target_index)
            if len(component) > 1:
                signals.add("dependency_cycle")
                affected.update((scope_index, index) for index in component)
    if unresolved:
        reasons.add("dependency_unresolved")
    return {
        "signal_codes": sorted(signals),
        "affected_count": len(affected),
        "activity_count": len(walked),
        "unresolved_dependency_count": unresolved,
        "coverage_status": "partial" if reasons and walked else "unknown" if reasons else "complete",
        "reason_codes": sorted(reasons),
    }
