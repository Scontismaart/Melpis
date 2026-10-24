from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.responses import JSONResponse

from src.api.routes.knowledge import salva_dati_struttura
from src.models.schemas import DatiStrutturaInput


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("service_result", "expected_status", "expected_detail"),
    [
        (
            {"doc_id": "doc-1", "index_status": "in_progress", "index_verified": False},
            202,
            "Dati struttura salvati; indicizzazione in corso.",
        ),
        (
            {"doc_id": None, "index_status": "not_indexed", "index_verified": False},
            200,
            "Dati struttura salvati; indicizzazione non verificata.",
        ),
        (
            {"doc_id": "doc-1", "index_status": "indexed", "index_verified": True},
            200,
            "Dati struttura e listino servizi aggiornati e indicizzati.",
        ),
    ],
)
async def test_route_reports_index_state_truthfully(service_result, expected_status, expected_detail):
    request = MagicMock()
    user = {"organization_id": "org-1"}
    data = DatiStrutturaInput(orari="09:00-17:00", servizi=[])
    repo = MagicMock()
    service = AsyncMock(return_value=service_result)

    with patch("src.api.routes.knowledge.get_repo", return_value=repo), \
         patch("src.api.routes.knowledge.check_feature_blocked_by_plan", new_callable=AsyncMock, return_value=None), \
         patch("src.api.routes.knowledge.enforce_org_rate_limit", new_callable=AsyncMock), \
         patch("src.core.documenti.dati_struttura.indicizza_dati_struttura", service):
        response = await salva_dati_struttura(data, request, user)

    if isinstance(response, JSONResponse):
        assert response.status_code == expected_status
        import json
        payload = json.loads(response.body)
    else:
        assert expected_status == 200
        payload = response
    assert payload["detail"] == expected_detail
    assert payload["index_status"] == service_result["index_status"]
    assert payload["index_verified"] is service_result["index_verified"]
