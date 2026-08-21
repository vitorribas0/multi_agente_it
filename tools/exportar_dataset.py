"""
Tool de exportação do dataset corrente para CSV ou Excel.

Salva em <BASE_DIR>/exports/ com nome único e devolve o download_url
servido por auditor.views.export_download.
"""
import json
import os
import re
import time
from pathlib import Path
from uuid import uuid4

from .registry import tool, publish_attachment


# ── Helpers ───────────────────────────────────────────────────────────

def _get_df(_session: dict):
    import pandas as pd
    rows = _session.get("athena_last_result")
    if not rows:
        return None
    return pd.DataFrame(rows)


def _err(msg: str) -> str:
    return json.dumps({"erro": msg}, ensure_ascii=False)


def _exports_dir() -> Path:
    """Resolve exports/ a partir do settings.BASE_DIR ou cwd como fallback."""
    try:
        from django.conf import settings
        base = Path(settings.BASE_DIR)
    except Exception:
        base = Path(os.getcwd())
    d = base / "exports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe_filename(stem: str) -> str:
    """Sanitiza o nome do arquivo: só [a-z0-9_-]."""
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", stem.strip())[:60]
    return stem or "export"


# ── Tool ──────────────────────────────────────────────────────────────

@tool(
    description=(
        "Exporta o dataset corrente para CSV ou Excel e devolve um "
        "download_url. O frontend renderiza um botão de download "
        "automaticamente.\n\n"
        "USE: quando o usuário pedir para baixar/exportar/salvar os "
        "resultados (após filtros, classificações, etc).\n\n"
        "FORMATOS: 'csv' (default) ou 'xlsx'."
    ),
    icon="💾",
)
def exportar_dataset(
    _session: dict,
    formato: str = "csv",
    nome: str = "",
) -> str:
    """Exporta o dataset corrente para CSV/XLSX.

    Args:
        formato: 'csv' (default) ou 'xlsx'.
        nome: Nome base do arquivo (sem extensão). Default: 'export_<timestamp>'.
    """
    df = _get_df(_session)
    if df is None:
        return _err("Nenhum dataset na sessão.")

    fmt = (formato or "csv").lower().strip()
    if fmt not in ("csv", "xlsx"):
        return _err(f"Formato '{formato}' inválido. Use 'csv' ou 'xlsx'.")

    stem = _safe_filename(nome) if nome else f"export_{int(time.time())}"
    short_id = uuid4().hex[:8]
    filename = f"{stem}_{short_id}.{fmt}"
    path = _exports_dir() / filename

    try:
        if fmt == "csv":
            df.to_csv(path, index=False, encoding="utf-8-sig")
        else:
            df.to_excel(path, index=False, engine="openpyxl")
    except Exception as e:
        return _err(f"Falha ao exportar: {e}")

    size_kb = round(path.stat().st_size / 1024, 1)
    cols = list(df.columns)
    payload = {
        "ok": True,
        "filename": filename,
        "download_url": f"/api/exports/{filename}",
        "formato": fmt,
        "linhas": len(df),
        "colunas": len(cols),
        # Nomes reais das colunas exportadas — deixa explícito QUAL dataset
        # foi salvo, expondo no retorno se o agente exportou o dataset
        # errado (ex.: o original intacto em vez do transformado).
        "colunas_nomes": cols if len(cols) <= 30 else cols[:30] + ["…"],
        "size_kb": size_kb,
    }

    # Publica como attachment da mensagem para que o card de download
    # apareça no chat (fora do bloco colapsável da tool), igual ao
    # card-tabela do consulta_aws.
    publish_attachment(_session, {"kind": "export", **payload})

    return json.dumps(payload, ensure_ascii=False)
