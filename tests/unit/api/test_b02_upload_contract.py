"""Actual credentialed preflight contract for resumable cross-origin uploads."""
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from src.api.cors import configure_cors


@pytest.mark.parametrize(('origin','status'), [('http://localhost:5173',200),('https://untrusted.example',400)])
def test_upload_preflight_with_idempotency_is_origin_scoped(origin, status):
    app=FastAPI()
    configure_cors(app, origins_csv='http://localhost:5173', public_app_url='')
    with TestClient(app) as client:
        response=client.options('/api/documenti/carica-file', headers={
            'Origin':origin,'Access-Control-Request-Method':'POST',
            'Access-Control-Request-Headers':'content-type,idempotency-key,x-csrf-token',
        })
    assert response.status_code==status
    if status==200:
        assert response.headers['access-control-allow-origin']==origin
        assert response.headers['access-control-allow-credentials']=='true'
        assert 'idempotency-key' in response.headers['access-control-allow-headers'].lower()
