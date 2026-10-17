#!/usr/bin/env python3
"""Audit dei contratti delle API route per garantire zero regressioni e zero auth drift (Invarianti 1, 2, 8).

Verifica automatica (Gate 2 del piano di refactoring Fase 3):
1. dump: estrae lo stato di tutte le route registrate in FastAPI (path, metodi, ruoli richiesti, response model)
   e lo salva in un file JSON baseline.
2. verify: confronta l'app attuale con il baseline salvato. Fallisce con exit code 1 se:
   - una rotta scompare o cambia path/metodo
   - una guardia di sicurezza (ruoli richiesti) viene rimossa, allentata o alterata
   - il modello di risposta cambia
   - le rotte critiche con rate limit LLM (LLM_ROUTES) non combaciano esattamente con i path attivi

Uso:
    python scripts/audit_route_contracts.py dump docs/route_contracts_baseline.json
    python scripts/audit_route_contracts.py verify docs/route_contracts_baseline.json
"""

import json
import os
import sys
from pathlib import Path

# Assicura che la root del progetto sia nel sys.path
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

LLM_ROUTES_EXPECTED = {"/api/messaggio", "/api/recensione", "/api/documenti/chiedi"}


def _extract_required_roles(dependency_callable) -> list[str] | None:
    """Estrae i ruoli richiesti da una dependency creata con require_ruolo."""
    if not callable(dependency_callable):
        return None
    # Verifica se è una closure generata da require_ruolo
    if hasattr(dependency_callable, "__closure__") and dependency_callable.__closure__:
        for cell in dependency_callable.__closure__:
            val = cell.cell_contents
            if isinstance(val, tuple) and any(r in ("owner", "manager", "staff") for r in val):
                return sorted(list(val))
    return None


def _collect_routes(routes) -> list:
    collected = []
    for r in routes:
        if type(r).__name__ == "APIRoute":
            collected.append(r)
        elif hasattr(r, "original_router") and r.original_router:
            collected.extend(_collect_routes(r.original_router.routes))
        elif hasattr(r, "routes"):
            collected.extend(_collect_routes(r.routes))
    return collected


def extract_route_contracts() -> dict:
    """Estrae i contratti di tutte le route definite in src.api.main.app."""
    # Imposta variabili ambiente minime per import sicuro senza chiamare rete
    os.environ.setdefault("DATABASE_URL", "")
    os.environ.setdefault("API_KEY_SERVICE", "audit-test-key")

    from src.api.main import app

    contracts = {}

    all_routes = _collect_routes(app.routes)

    for route in all_routes:

        # Ispezione dipendenze per autorizzazioni e ruoli
        required_roles = None
        has_org_context = False

        for dep in route.dependant.dependencies:
            call = dep.call
            roles = _extract_required_roles(call)
            if roles:
                required_roles = roles
            call_name = getattr(call, "__name__", "")
            if call_name in ("get_organization_context", "get_current_org_id"):
                has_org_context = True

        # Some high-risk routes must resolve authorization against a
        # resource-bound tenant inside the handler (for example a one-time
        # export token) rather than invoking the general JIT org resolver.
        explicit_roles = getattr(route.endpoint, "__api_required_roles__", None)
        if explicit_roles:
            required_roles = sorted(explicit_roles)
        has_org_context = has_org_context or bool(
            getattr(route.endpoint, "__api_org_context__", False)
        )


        key = f"{route.path}::{','.join(sorted(route.methods))}"
        contracts[key] = {
            "path": route.path,
            "methods": sorted(list(route.methods)),
            "endpoint_name": route.endpoint.__name__,
            "required_roles": required_roles,
            "has_org_context": has_org_context,
            "response_model": str(route.response_model) if route.response_model else None,
            "is_llm_route": route.path in LLM_ROUTES_EXPECTED,
        }

    return dict(sorted(contracts.items()))


def dump_baseline(output_file: str) -> None:
    """Cattura lo snapshot dei contratti e lo scrive su file JSON."""
    contracts = extract_route_contracts()
    path = Path(output_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(contracts, f, indent=2, ensure_ascii=False)
    print(f"ROUTE CONTRACT AUDIT: Baseline salvato in {output_file} ({len(contracts)} rotte censite).")


def verify_baseline(baseline_file: str) -> int:
    """Verifica che l'app attuale rispetti rigorosamente il baseline salvato."""
    path = Path(baseline_file)
    if not path.exists():
        print(f"ERRORE: File baseline {baseline_file} non trovato. Esegui prima 'dump'.", file=sys.stderr)
        return 1

    with open(path, "r", encoding="utf-8") as f:
        baseline = json.load(f)

    current = extract_route_contracts()

    drifts = []

    # 1. Rotte mancanti
    for key, base_info in baseline.items():
        if key not in current:
            drifts.append(f"ROTTA MANCANTE: {key} (definita nel baseline ma assente nell'app)")
            continue

        curr_info = current[key]

        # 2. Verifica ruoli di autorizzazione (Invariante 1)
        if base_info["required_roles"] != curr_info["required_roles"]:
            drifts.append(
                f"AUTH DRIFT su {key}: ruoli baseline={base_info['required_roles']} vs attuali={curr_info['required_roles']}"
            )

        # 3. Verifica response model
        if base_info["response_model"] != curr_info["response_model"]:
            drifts.append(
                f"RESPONSE MODEL DRIFT su {key}: baseline={base_info['response_model']} vs attuale={curr_info['response_model']}"
            )

        # 4. Verifica LLM route flag (Invariante 8)
        if base_info["is_llm_route"] != curr_info["is_llm_route"]:
            drifts.append(f"LLM ROUTE DRIFT su {key}: is_llm_route disallineato!")

    # 5. Rotte non censite o inattese
    for key in current:
        if key not in baseline:
            drifts.append(f"ROTTA EXTRA INATTESA: {key} (presente nell'app ma non nel baseline)")

    # 6. Verifica copertura esatta di LLM_ROUTES_EXPECTED
    active_llm_paths = {c["path"] for c in current.values() if c["is_llm_route"]}
    if active_llm_paths != LLM_ROUTES_EXPECTED:
        drifts.append(
            f"LLM_ROUTES DISALLINEATO: attese={sorted(LLM_ROUTES_EXPECTED)} vs censite={sorted(active_llm_paths)}"
        )

    if drifts:
        print("[FAIL] ROUTE CONTRACT AUDIT FAILED! Rilevati i seguenti drift di contratto:", file=sys.stderr)
        for d in drifts:
            print(f"  - {d}", file=sys.stderr)
        return 1

    print(f"[OK] ROUTE CONTRACT AUDIT PASSED: 0 drift rilevati. Tutte le {len(current)} rotte combaciano esattamente.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Uso: python scripts/audit_route_contracts.py [dump|verify] <path_json>")
        sys.exit(1)

    cmd = sys.argv[1].lower()
    target_path = sys.argv[2]

    if cmd == "dump":
        dump_baseline(target_path)
        sys.exit(0)
    elif cmd == "verify":
        sys.exit(verify_baseline(target_path))
    else:
        print(f"Comando sconosciuto: {cmd}. Usa 'dump' o 'verify'.")
        sys.exit(1)
