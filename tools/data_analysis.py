"""
Tools de análise de dados sobre o dataset corrente da sessão.

Convenção: o dataset "atual" da conversa fica em
`_session['athena_last_result']` (lista de dicts) e
`_session['athena_last_columns']` (lista de colunas).

Tools que transformam (normalizar, filtrar) atualizam esses campos in-place.
Tools que apenas analisam (agrupar, contar, regex) retornam o resultado
sem modificar o dataset.
"""
import json
import re
import unicodedata

from .registry import tool


# ── Helpers internos ────────────────────────────────────────────────

def _get_df(_session: dict):
    """Reconstrói um DataFrame a partir do dataset corrente em sessão."""
    import pandas as pd

    rows = _session.get("athena_last_result")
    if not rows:
        return None
    return pd.DataFrame(rows)


def _save_df(df, _session: dict) -> None:
    # df.to_dict preserva NaN/NaT/numpy types — o JSONField emite `NaN`
    # literal, que não é JSON válido e o SQLite rejeita via CHECK constraint.
    raw = df.to_json(orient="records", date_format="iso", default_handler=str)
    _session["athena_last_result"] = json.loads(raw)
    _session["athena_last_columns"] = list(df.columns)


def _normalize_text(value) -> str:
    if value is None:
        return ""
    s = str(value).lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^\w\s]", " ", s, flags=re.UNICODE)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _err(msg: str) -> str:
    return json.dumps({"erro": msg}, ensure_ascii=False)


# ── Tools ───────────────────────────────────────────────────────────

@tool(
    description=(
        "Descreve o dataset corrente da sessão: shape (linhas × colunas), "
        "dtypes, contagem de nulos por coluna e amostra das 3 primeiras "
        "linhas. "
        "USE: PRIMEIRO PASSO obrigatório de qualquer análise — você não "
        "sabe o que está em sessão sem chamar isto. "
        "NÃO use: se já chamou descrever_dataset neste turno e nada foi "
        "alterado depois (resultado já está no histórico)."
    ),
    icon="🔎",
)
def descrever_dataset(_session: dict) -> str:
    """Descreve o dataset corrente."""
    df = _get_df(_session)
    if df is None:
        return _err("Nenhum dataset na sessão. Carregue dados antes (ex.: consulta_aws).")

    info = {
        "linhas": len(df),
        "colunas": list(df.columns),
        "dtypes": {c: str(df[c].dtype) for c in df.columns},
        "nulos_por_coluna": {c: int(df[c].isna().sum()) for c in df.columns},
        "amostra": df.head(3).to_dict(orient="records"),
    }
    return json.dumps(info, ensure_ascii=False, default=str)


@tool(
    description=(
        "Cria uma versão normalizada de uma coluna de texto (lowercase, "
        "sem acento, sem pontuação, espaços colapsados) numa nova coluna "
        "'<coluna>__norm'. "
        "USE: SEMPRE antes de buscar palavras-chave, filtrar por termo "
        "ou aplicar regex sobre texto livre — buscas precisam ser "
        "case/accent-insensitive. "
        "NÃO use: para colunas numéricas, datas ou IDs."
    ),
    icon="✨",
)
def normalizar_coluna(coluna: str, _session: dict) -> str:
    """Normaliza uma coluna de texto criando '<coluna>__norm'.

    Args:
        coluna: Nome da coluna de texto livre (ex.: 'descricao', 'relato').
    """
    df = _get_df(_session)
    if df is None:
        return _err("Nenhum dataset na sessão.")
    if coluna not in df.columns:
        return _err(f"Coluna '{coluna}' não existe. Disponíveis: {list(df.columns)}")

    nova = f"{coluna}__norm"
    df[nova] = df[coluna].map(_normalize_text)
    _save_df(df, _session)
    return json.dumps(
        {
            "ok": True,
            "coluna_origem": coluna,
            "coluna_normalizada": nova,
            "amostra": df[[coluna, nova]].head(3).to_dict(orient="records"),
        },
        ensure_ascii=False,
        default=str,
    )


@tool(
    description=(
        "Filtra o dataset corrente IN-PLACE mantendo apenas linhas em "
        "que 'coluna' contém (modo='contem') ou não contém "
        "(modo='nao_contem') o 'termo'. "
        "USE: para reduzir o dataset antes de análises subsequentes. "
        "ATENÇÃO: a operação é destrutiva — operações posteriores rodam "
        "sobre o filtrado. Para texto, prefira passar a coluna normalizada "
        "(sufixo '__norm') e o termo já em minúsculas/sem acento."
    ),
    icon="🔬",
)
def filtrar_por_termo(
    coluna: str,
    termo: str,
    _session: dict,
    modo: str = "contem",
) -> str:
    """Filtra o dataset in-place por presença/ausência de termo.

    Args:
        coluna: Coluna a inspecionar. Para texto, prefira '<coluna>__norm'.
        termo: Termo de busca. Se for em coluna __norm, passe lowercase/sem acento.
        modo: 'contem' (default) mantém quem contém; 'nao_contem' mantém quem NÃO contém.
    """
    df = _get_df(_session)
    if df is None:
        return _err("Nenhum dataset na sessão.")
    if coluna not in df.columns:
        return _err(f"Coluna '{coluna}' não existe. Disponíveis: {list(df.columns)}")
    if modo not in ("contem", "nao_contem"):
        return _err("Parâmetro 'modo' deve ser 'contem' ou 'nao_contem'.")

    serie = df[coluna].astype(str)
    mask = serie.str.contains(re.escape(termo), case=False, na=False, regex=True)
    if modo == "nao_contem":
        mask = ~mask

    antes = len(df)
    df_filtrado = df[mask].copy()
    _save_df(df_filtrado, _session)

    return json.dumps(
        {
            "ok": True,
            "coluna": coluna,
            "termo": termo,
            "modo": modo,
            "linhas_antes": antes,
            "linhas_depois": len(df_filtrado),
        },
        ensure_ascii=False,
    )


@tool(
    description=(
        "Conta a ocorrência de cada palavra-chave numa coluna do dataset "
        "corrente: para cada palavra, retorna em quantas linhas aparece "
        "e o percentual. "
        "USE: para medir penetração de N termos num único passo (mais "
        "eficiente que N chamadas a contem_termo). "
        "PRÉ-REQUISITO: as palavras-chave SEMPRE vêm do usuário — peça "
        "via ask_human antes de chamar esta tool. "
        "DICA: passe a coluna normalizada ('<coluna>__norm') e palavras "
        "também em lowercase/sem acento."
    ),
    icon="🔠",
)
def contar_keywords(
    coluna: str,
    palavras: list,
    _session: dict,
) -> str:
    """Conta ocorrências de palavras-chave numa coluna.

    Args:
        coluna: Coluna a inspecionar. Prefira '<coluna>__norm' para texto.
        palavras: Lista de palavras-chave (strings). Para colunas __norm, em lowercase e sem acento.
    """
    df = _get_df(_session)
    if df is None:
        return _err("Nenhum dataset na sessão.")
    if coluna not in df.columns:
        return _err(f"Coluna '{coluna}' não existe. Disponíveis: {list(df.columns)}")
    if not palavras:
        return _err("Lista 'palavras' vazia — peça as palavras-chave ao usuário.")

    serie = df[coluna].astype(str)
    total = len(df)
    resultado = []
    for p in palavras:
        if not isinstance(p, str) or not p.strip():
            continue
        mask = serie.str.contains(re.escape(p), case=False, na=False, regex=True)
        n = int(mask.sum())
        resultado.append({
            "palavra": p,
            "linhas_com_termo": n,
            "percentual": round(100 * n / total, 2) if total else 0.0,
        })

    return json.dumps(
        {"coluna": coluna, "total_linhas": total, "resultados": resultado},
        ensure_ascii=False,
    )


@tool(
    description=(
        "Verifica se um termo aparece numa coluna: retorna quantas "
        "linhas contêm, o percentual e até 3 exemplos. "
        "USE: para checagem rápida 'tem ou não tem X aqui?' com amostras "
        "para inspeção. "
        "Para múltiplos termos de uma vez, prefira contar_keywords. "
        "Para texto, prefira a coluna normalizada (sufixo '__norm')."
    ),
    icon="✅",
)
def contem_termo(coluna: str, termo: str, _session: dict) -> str:
    """Checa se um termo ocorre numa coluna e devolve exemplos.

    Args:
        coluna: Coluna a inspecionar. Prefira '<coluna>__norm' para texto.
        termo: Termo de busca. Em colunas __norm, lowercase/sem acento.
    """
    df = _get_df(_session)
    if df is None:
        return _err("Nenhum dataset na sessão.")
    if coluna not in df.columns:
        return _err(f"Coluna '{coluna}' não existe. Disponíveis: {list(df.columns)}")

    serie = df[coluna].astype(str)
    mask = serie.str.contains(re.escape(termo), case=False, na=False, regex=True)
    n = int(mask.sum())
    total = len(df)
    exemplos = df[mask].head(3).to_dict(orient="records")
    return json.dumps(
        {
            "coluna": coluna,
            "termo": termo,
            "linhas_com_termo": n,
            "total_linhas": total,
            "percentual": round(100 * n / total, 2) if total else 0.0,
            "exemplos": exemplos,
        },
        ensure_ascii=False,
        default=str,
    )


@tool(
    description=(
        "Agrupa o dataset por uma coluna e aplica uma agregação. "
        "USE: para distribuição/ranking por categoria (ex.: contagem por "
        "nomeassunto, soma por mês, média por tipopessoa). "
        "Agregações suportadas: 'count' (default), 'sum', 'mean', 'min', "
        "'max', 'nunique'. Para sum/mean/min/max informe também "
        "'coluna_metrica' (a coluna numérica a agregar). "
        "Retorna até 'top_n' (default 20) grupos ordenados pelo valor."
    ),
    icon="📊",
)
def agrupar(
    coluna: str,
    _session: dict,
    agg: str = "count",
    coluna_metrica: str = "",
    top_n: int = 20,
) -> str:
    """Agrupa o dataset por uma coluna e aplica uma agregação.

    Args:
        coluna: Coluna pela qual agrupar (categoria).
        agg: 'count' (default) | 'sum' | 'mean' | 'min' | 'max' | 'nunique'.
        coluna_metrica: Coluna numérica a agregar. Obrigatória para sum/mean/min/max.
        top_n: Quantos grupos retornar (default 20, ordenados desc).
    """
    df = _get_df(_session)
    if df is None:
        return _err("Nenhum dataset na sessão.")
    if coluna not in df.columns:
        return _err(f"Coluna '{coluna}' não existe. Disponíveis: {list(df.columns)}")

    agg = (agg or "count").lower()
    permitidos = {"count", "sum", "mean", "min", "max", "nunique"}
    if agg not in permitidos:
        return _err(f"Agregação '{agg}' inválida. Use uma de: {sorted(permitidos)}.")

    try:
        if agg == "count":
            serie = df.groupby(coluna).size().sort_values(ascending=False)
            label = "count"
        else:
            if not coluna_metrica:
                return _err(
                    f"Agregação '{agg}' requer 'coluna_metrica' (coluna numérica)."
                )
            if coluna_metrica not in df.columns:
                return _err(
                    f"coluna_metrica '{coluna_metrica}' não existe. "
                    f"Disponíveis: {list(df.columns)}"
                )
            serie = (
                df.groupby(coluna)[coluna_metrica]
                .agg(agg)
                .sort_values(ascending=False)
            )
            label = f"{agg}({coluna_metrica})"
    except Exception as e:
        return _err(f"Falha ao agrupar: {e}")

    top = serie.head(top_n)
    return json.dumps(
        {
            "coluna": coluna,
            "agregacao": label,
            "grupos_total": int(serie.shape[0]),
            "top": [
                {coluna: idx, label: (float(v) if hasattr(v, "item") else v)}
                for idx, v in top.items()
            ],
        },
        ensure_ascii=False,
        default=str,
    )


@tool(
    description=(
        "Aplica uma expressão regular sobre uma coluna e retorna "
        "estatísticas: linhas com pelo menos um match, total de matches "
        "e os 'top_n' valores capturados mais frequentes. "
        "USE: para extrair padrões estruturados (CPFs, valores R$, "
        "códigos, datas) de texto livre. "
        "Para texto livre, prefira a coluna normalizada (sufixo '__norm')."
    ),
    icon="🧩",
)
def regex_extrair(
    coluna: str,
    padrao: str,
    _session: dict,
    top_n: int = 20,
) -> str:
    """Extrai padrões via regex de uma coluna.

    Args:
        coluna: Coluna a inspecionar. Prefira '<coluna>__norm' para texto livre.
        padrao: Regex Python (re). Use grupos de captura para extrair partes específicas.
        top_n: Quantas capturas mais frequentes retornar (default 20).
    """
    df = _get_df(_session)
    if df is None:
        return _err("Nenhum dataset na sessão.")
    if coluna not in df.columns:
        return _err(f"Coluna '{coluna}' não existe. Disponíveis: {list(df.columns)}")

    try:
        regex = re.compile(padrao)
    except re.error as e:
        return _err(f"Regex inválida: {e}")

    serie = df[coluna].astype(str)
    matches_por_linha = serie.map(lambda v: regex.findall(v))

    todos = []
    linhas_com_match = 0
    for lst in matches_por_linha:
        if lst:
            linhas_com_match += 1
            for m in lst:
                # findall pode devolver tuplas se houver grupos
                todos.append(m if isinstance(m, str) else " | ".join(m))

    from collections import Counter
    top = Counter(todos).most_common(top_n)

    return json.dumps(
        {
            "coluna": coluna,
            "padrao": padrao,
            "linhas_com_match": linhas_com_match,
            "total_matches": len(todos),
            "top_capturas": [{"valor": v, "freq": n} for v, n in top],
        },
        ensure_ascii=False,
    )
