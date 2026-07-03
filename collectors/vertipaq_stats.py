# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""VertiPaq Analyzer statistics for semantic models (Import + Direct Lake).

Runs **inside Fabric** and uses semantic-link-labs'
``sempy_labs.vertipaq_analyzer`` to capture the same storage metrics DAX Studio
shows: per-table and per-column total / data / dictionary / hierarchy size,
cardinality, encoding, data type and % of the model. This is the deep
"VertiPaq burden" view the metadata-only ``semantic_model_definitions``
collector cannot produce (sizes require the XMLA / storage-engine DMVs).

Model list comes from ``semantic_models.json`` (already scoped by
``WORKSPACE_IDS``) so we re-use the inventory the other collectors built.

Each model is analyzed in isolation: a failure (no XMLA access, model not
resident, transient error) is captured per-model and the run continues.

DATA SAFETY: storage *metadata* only — table/column names, sizes, encoding,
data type and % of the model all come from the storage-engine DMVs and need no
data query. Exact column **cardinality** (distinct counts) requires a COUNT-style
DAX query over the model, so it is **opt-in** via ``VERTIPAQ_STATS_READ_DATA=true``
(default off). Even when enabled, only aggregate counts are returned — never
business row values, and nothing is persisted beyond sizes and counts.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

# Statistic DataFrames returned by sempy_labs.vertipaq_analyzer that we persist.
# Each output key maps to the set of DataFrame labels different labs versions use
# for it (e.g. newer builds return "Model Summary" where older ones returned
# "Model"). The first label present in the result wins.
_WANTED_FRAMES = {
    "model": ("Model", "Model Summary"),
    "tables": ("Tables",),
    "columns": ("Columns",),
    "partitions": ("Partitions",),
    "relationships": ("Relationships",),
    "hierarchies": ("Hierarchies",),
}


def _truthy(value: Optional[str]) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "y", "on")


# Symbols newer ``azure-core`` (>= 1.31) exposes that some ``semantic-link-labs``
# builds import at module-load time. Older Fabric runtimes ship an azure-core
# without them, so the bare ``import sempy_labs`` fails with
# ``cannot import name '<X>' from 'azure.core.credentials'``. Stand-ins with the
# right shape live here; anything not listed gets a generic stub on demand.


def _make_credential_stub(name: str):
    """Build a permissive stand-in for an ``azure.core.credentials`` symbol."""
    if name == "AccessTokenInfo":
        class AccessTokenInfo:  # azure-core >= 1.31
            def __init__(self, token, expires_on, *, token_type="Bearer", refresh_on=None):
                self.token = token
                self.expires_on = expires_on
                self.token_type = token_type
                self.refresh_on = refresh_on

        return AccessTokenInfo
    if name == "TokenRequestOptions":
        class TokenRequestOptions(dict):
            pass

        return TokenRequestOptions
    # SupportsTokenInfo, TokenProvider and any other protocol/typing helper:
    # an empty class is enough to satisfy ``from azure.core.credentials import X``.
    return type(name, (), {})


_MISSING_NAME_RE = re.compile(
    r"cannot import name ['\"](?P<name>\w+)['\"] from ['\"]azure\.core\.credentials['\"]"
)


def _shim_azure_core_credentials(names=None) -> None:
    """Inject permissive stand-ins for credential symbols the runtime's
    azure-core lacks.

    Upgrading azure-core in place is risky — it is already imported and shared
    with the auth stack used by the other collectors — so instead we add stubs
    for the missing names. They satisfy the import; sempy_labs still performs
    real token handling through sempy's own (working) Fabric authentication.
    No-op when azure-core already provides the names.
    """
    try:
        import azure.core.credentials as _cred
    except Exception:
        return
    for name in (names or ("AccessTokenInfo", "TokenRequestOptions", "SupportsTokenInfo", "TokenProvider")):
        if not hasattr(_cred, name):
            setattr(_cred, name, _make_credential_stub(name))


def _import_sempy_labs_with_shim():
    """``import sempy_labs``, healing missing azure-core credential symbols.

    Some runtimes are missing several newer names (``AccessTokenInfo``,
    ``TokenProvider``, ...). Rather than enumerate every one, we retry the import
    and, whenever it fails with ``cannot import name '<X>' from
    'azure.core.credentials'``, inject a stub for ``<X>`` and try again.
    """
    import sys

    def _purge() -> None:
        for _m in [m for m in list(sys.modules) if m == "sempy_labs" or m.startswith("sempy_labs.")]:
            del sys.modules[_m]

    _shim_azure_core_credentials()
    last_exc = None
    for _ in range(12):  # bounded so a non-credential failure can't loop forever
        try:
            _purge()
            import sempy_labs  # noqa: F401
            return None
        except ImportError as exc:
            last_exc = exc
            match = _MISSING_NAME_RE.search(str(exc))
            if not match:
                return str(exc)  # not a credential-symbol problem -> give up
            _shim_azure_core_credentials([match.group("name")])
        except Exception as exc:  # pragma: no cover - depends on runtime
            return str(exc)
    return str(last_exc) if last_exc else "sempy_labs import did not converge"


def _ensure_sempy_labs() -> Optional[str]:
    """Import semantic-link-labs, installing it on first use if needed.

    Returns ``None`` on success or an error string describing why it is
    unavailable. We first try a ``--no-deps`` install (semantic-link / sempy,
    pandas and the Analysis Services client libraries are already provisioned in
    the Fabric runtime, so this avoids disturbing the pinned azure-core /
    azure-identity versions the rest of the pipeline depends on). If the import
    still fails — usually because a small pure-Python dependency such as
    ``anytree`` or ``jsonpath-ng`` is missing — we fall back to a normal install
    that pulls those in. Each import attempt heals any credential symbols the
    runtime's older azure-core lacks (see :func:`_import_sempy_labs_with_shim`).
    """
    import subprocess
    import sys

    # 0) maybe it is already importable once we shim the missing symbols
    err0 = _import_sempy_labs_with_shim()
    if err0 is None:
        return None

    def _try_install(args: List[str]) -> Optional[str]:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", *args], check=False)
        return _import_sempy_labs_with_shim()

    # 1) lean install that leaves the pinned azure-* packages untouched
    err = _try_install(["--no-deps", "semantic-link-labs"])
    if err is None:
        return None
    # 2) fall back to a full install so missing pure-Python deps get pulled in
    err2 = _try_install(["semantic-link-labs"])
    if err2 is None:
        return None
    return f"--no-deps import failed ({err}); full install import failed ({err2})"


def _shim_fabric_rest_client() -> None:
    """Let an older runtime ``FabricRestClient`` accept newer labs' ``credential`` kwarg.

    Newer ``semantic-link-labs`` builds construct the client as
    ``FabricRestClient(credential=token)``. Older bundled ``sempy`` runtimes only
    accept ``FabricRestClient(token_provider=..., retry_config=...)`` and raise
    ``TypeError: ... unexpected keyword argument 'credential'``. When
    ``token_provider`` is omitted the client uses the notebook's default identity
    — which is exactly the identity the rest of the pipeline already
    authenticates with — so we simply drop the unsupported ``credential`` kwarg.

    No-op when the runtime's client already accepts ``credential`` (or **kwargs),
    or when the shim has already been applied.
    """
    import inspect

    try:
        import sempy.fabric as _fabric
    except Exception:
        return
    cls = getattr(_fabric, "FabricRestClient", None)
    if cls is None or getattr(cls, "_arch_review_cred_shim", False):
        return
    try:
        params = inspect.signature(cls.__init__).parameters
    except (TypeError, ValueError):
        return
    # Already compatible: explicit `credential` param or a **kwargs catch-all.
    if "credential" in params or any(p.kind == p.VAR_KEYWORD for p in params.values()):
        return
    _orig_init = cls.__init__

    def _patched_init(self, *args, credential=None, **kwargs):
        # drop `credential` -> default-identity client (what the notebook uses)
        return _orig_init(self, *args, **kwargs)

    cls.__init__ = _patched_init
    cls._arch_review_cred_shim = True



def _norm_key(name: str) -> str:
    """Normalize a VertiPaq Analyzer column header to snake_case.

    ``"% DB"`` -> ``"pct_db"``, ``"Total Size"`` -> ``"total_size"``,
    ``"Column Name"`` -> ``"column_name"``.
    """
    s = str(name).strip().lower().replace("%", " pct ")
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_")


def _frame_to_records(df: Any) -> List[Dict[str, Any]]:
    """Convert a pandas DataFrame to JSON-safe records with normalized keys."""
    try:
        renamed = df.rename(columns={c: _norm_key(c) for c in df.columns})
        records = renamed.to_dict(orient="records")
    except Exception:  # pragma: no cover - defensive
        return []
    out: List[Dict[str, Any]] = []
    for rec in records:
        clean: Dict[str, Any] = {}
        for k, v in rec.items():
            # pandas may carry numpy scalars / NaT / NaN; coerce to native.
            try:
                if v is None:
                    clean[k] = None
                elif hasattr(v, "item"):
                    clean[k] = v.item()
                else:
                    clean[k] = v
            except Exception:
                clean[k] = str(v)
            # JSON cannot encode NaN; turn floats that aren't finite into None.
            val = clean[k]
            if isinstance(val, float) and (val != val or val in (float("inf"), float("-inf"))):
                clean[k] = None
        out.append(clean)
    return out


def _analyze_model(dataset: str, workspace: Optional[str], read_stats: bool) -> Dict[str, List[Dict[str, Any]]]:
    """Run the VertiPaq analyzer for one model and return its stat frames.

    ``read_stats`` maps to ``read_stats_from_data``: when ``False`` (default) the
    analyzer reports sizes/encoding from storage metadata only; when ``True`` it
    additionally issues COUNT-style DAX to compute exact column cardinality.
    """
    import sempy_labs  # imported lazily so the collector degrades gracefully

    # Older bundled sempy runtimes reject newer labs' FabricRestClient(credential=...).
    _shim_fabric_rest_client()

    result = sempy_labs.vertipaq_analyzer(
        dataset=dataset,
        workspace=workspace,
        read_stats_from_data=read_stats,
    )
    frames: Dict[str, List[Dict[str, Any]]] = {key: [] for key in _WANTED_FRAMES}
    if isinstance(result, dict):
        for key, labels in _WANTED_FRAMES.items():
            for label in labels:
                df = result.get(label)
                if df is not None:
                    frames[key] = _frame_to_records(df)
                    break
    return frames


def collect(output_dir: str | os.PathLike = "output/raw") -> Path:
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "vertipaq_stats.json"

    if _truthy(os.environ.get("VERTIPAQ_STATS_SKIP")):
        payload = {
            "available": False,
            "skipped": True,
            "models": [],
            "notes": ["VERTIPAQ_STATS_SKIP is set; VertiPaq analysis was not run."],
        }
        target.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"VertiPaq stats: skipped (VERTIPAQ_STATS_SKIP). Wrote {target}.")
        return target

    # semantic-link-labs is only available inside a Fabric notebook runtime.
    import_error = _ensure_sempy_labs()
    if import_error is not None:
        payload = {
            "available": False,
            "models": [],
            "notes": [
                "sempy_labs (semantic-link-labs) is not importable here; VertiPaq "
                "analysis only runs inside a Fabric notebook. " + import_error
            ],
        }
        target.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"VertiPaq stats: sempy_labs unavailable ({import_error}) -> wrote empty {target}.")
        return target

    catalog_path = target_dir / "semantic_models.json"
    try:
        catalog = json.loads(catalog_path.read_text(encoding="utf-8-sig"))
    except Exception:
        catalog = {}
    datasets: List[Dict[str, Any]] = catalog.get("datasets") or []

    if not datasets:
        payload = {
            "available": True,
            "models": [],
            "notes": ["No datasets found in semantic_models.json to analyze."],
        }
        target.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"VertiPaq stats: no datasets in scope. Wrote {target}.")
        return target

    print(f"VertiPaq stats: analyzing {len(datasets)} model(s)...")
    read_stats = _truthy(os.environ.get("VERTIPAQ_STATS_READ_DATA"))
    if read_stats:
        print("  VERTIPAQ_STATS_READ_DATA is set: computing exact column cardinality "
              "(aggregate COUNT-style DAX over each model).")
    else:
        print("  metadata-only (sizes + encoding, no cardinality). "
              "Set VERTIPAQ_STATS_READ_DATA=true to add exact column cardinality.")
    models_out: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []
    for i, ds in enumerate(datasets, 1):
        model_id = ds.get("id")
        model_name = ds.get("name")
        ws_id = ds.get("workspaceId") or ds.get("groupId")
        ws_name = ds.get("workspaceName")
        if not model_id:
            continue
        try:
            frames = _analyze_model(model_id, ws_id or ws_name, read_stats)
            models_out.append({
                "model_id": model_id,
                "model_name": model_name,
                "workspace_name": ws_name,
                "storage_mode": ds.get("targetStorageMode"),
                **frames,
            })
        except Exception as exc:  # one bad model must not stop the rest
            errors.append({"model_id": str(model_id), "model_name": str(model_name), "error": str(exc)})
            print(f"  model {model_name} ({model_id}) failed: {exc}")
        if i % 10 == 0:
            print(f"  ... models {i}/{len(datasets)}")

    payload = {
        "available": True,
        "read_stats_from_data": read_stats,
        "models": models_out,
        "errors": errors,
    }
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {target} ({len(models_out)} model(s) analyzed, {len(errors)} error(s)).")
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="output/raw")
    args = parser.parse_args()
    collect(output_dir=args.output_dir)


if __name__ == "__main__":
    main()
