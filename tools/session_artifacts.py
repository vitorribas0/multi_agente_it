"""Localização segura de saídas por conversa.

Quando uma tool é executada dentro de um chat, o arquivo final fica na caixa
isolada daquela conversa em ``saida/``. O diretório global ``exports/`` continua como
fallback para chamadas antigas ou sem contexto de conversa.
"""
from __future__ import annotations

import os
from pathlib import Path


def _base_dir() -> Path:
    try:
        from django.conf import settings
        return Path(settings.BASE_DIR)
    except Exception:
        return Path(os.getcwd())


def conversation_id(session: dict | None) -> int | None:
    value = (session or {}).get("__conversation_id")
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def artifact_dir(session: dict | None) -> Path:
    """Retorna a pasta de saída da conversa ou o fallback legado."""
    conv_id = conversation_id(session)
    if conv_id is None:
        target = _base_dir() / "exports"
    else:
        target = _base_dir() / "runtime" / "codex_sessions" / str(conv_id) / "saida"
    target.mkdir(parents=True, exist_ok=True)
    return target


def artifact_download_url(session: dict | None, filename: str) -> str:
    """Monta a URL de download correspondente à pasta que contém o arquivo."""
    conv_id = conversation_id(session)
    if conv_id is None:
        return f"/api/exports/{filename}"
    return f"/api/conversations/{conv_id}/artifacts/{filename}"
