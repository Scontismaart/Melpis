"""Shared CORS policy for the API and isolated contract tests."""

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


def configure_cors(
    app: FastAPI,
    *,
    origins_csv: str | None = None,
    public_app_url: str | None = None,
) -> None:
    """Install the credentialed CORS policy used by the application."""
    configured_origins = (
        os.getenv("CORS_ORIGINS", "http://localhost:5173")
        if origins_csv is None
        else origins_csv
    )
    allow_origins = [origin.strip() for origin in configured_origins.split(",") if origin.strip()]
    public_url = (
        (os.getenv("PUBLIC_APP_URL") or "").strip().rstrip("/")
        if public_app_url is None
        else public_app_url.strip().rstrip("/")
    )
    if public_url and public_url not in allow_origins:
        allow_origins.append(public_url)
    if not allow_origins:
        raise RuntimeError(
            "CORS_ORIGINS e' impostata ma vuota dopo il parsing. "
            "Imposta una lista di origini valide separate da virgola, "
            "o rimuovi la variabile per usare il default locale."
        )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=[
            "Authorization",
            "X-Organization-Id",
            "X-API-Key",
            "X-CSRF-Token",
            "Content-Type",
        ],
    )
