"""
Tool de execução de código pandas — canivete suíço de análise.

O agente escreve código Python com pandas/numpy/regex sobre o dataset
corrente da sessão. Não-destrutivo por default: só modifica o dataset
se o código atribuir a `result_df`.

Sandbox restritivo: bloqueia open/exec/eval/import/__builtins__ — os
módulos pd/np/re já vêm injetados.
"""
import io
import json
import re as _re
import contextlib

from .registry import tool


# ── Helpers ───────────────────────────────────────────────────────────

def _get_df(_session: dict):
    import pandas as pd
    rows = _session.get("athena_last_result")
    if not rows:
        return None
    return pd.DataFrame(rows)


def _save_df(df, _session: dict) -> None:
    raw = df.to_json(orient="records", date_format="iso", default_handler=str)
    _session["athena_last_result"] = json.loads(raw)
    _session["athena_last_columns"] = list(df.columns)


def _get_named_datasets(_session: dict) -> dict:
    """Retorna dict {nome: DataFrame} dos datasets nomeados salvos na sessão."""
    import pandas as pd
    stored = _session.get("named_datasets") or {}
    result = {}
    for name, records in stored.items():
        if records:
            result[name] = pd.DataFrame(records)
    return result


def _save_named_dataset(name: str, df, _session: dict) -> None:
    """Salva um DataFrame como dataset nomeado na sessão (sem sobrescrever o corrente)."""
    import pandas as pd
    if not isinstance(df, pd.DataFrame):
        raise TypeError(f"save_dataset espera DataFrame, recebido {type(df).__name__}")
    raw = df.to_json(orient="records", date_format="iso", default_handler=str)
    if "named_datasets" not in _session:
        _session["named_datasets"] = {}
    _session["named_datasets"][name] = json.loads(raw)


def _err(msg: str) -> str:
    return json.dumps({"erro": msg}, ensure_ascii=False)


# Built-ins permitidos no sandbox. Tudo que não está aqui não existe
# para o código do agente — sem open, __import__, eval, exec, compile.
_SAFE_BUILTINS = {
    "abs", "all", "any", "bool", "dict", "enumerate", "filter",
    "float", "int", "len", "list", "map", "max", "min", "print",
    "range", "reversed", "round", "set", "slice", "sorted", "str",
    "sum", "tuple", "type", "zip", "isinstance", "True", "False", "None",
}

_BLOCKED_PATTERNS = [
    (r"\b__\w+__\b", "uso de dunder (ex: __import__, __class__) é proibido"),
    (r"\bopen\s*\(", "open() é proibido"),
    (r"\bexec\s*\(", "exec() é proibido"),
    (r"\beval\s*\(", "eval() é proibido"),
    (r"\bcompile\s*\(", "compile() é proibido"),
    (r"^\s*import\s", "import é proibido — pd/np/re já estão disponíveis"),
    (r"^\s*from\s+\w+\s+import", "import é proibido — pd/np/re já estão disponíveis"),
]


def _check_code_safety(code: str) -> str | None:
    """Retorna mensagem de erro se o código violar uma regra; None se ok."""
    for pattern, motivo in _BLOCKED_PATTERNS:
        if _re.search(pattern, code, flags=_re.MULTILINE):
            return motivo
    return None


# ── Tool ──────────────────────────────────────────────────────────────

@tool(
    description=(
        "Executa código Python (pandas) sobre o dataset corrente — o "
        "canivete suíço de análise. Use para casos que as outras tools "
        "não cobrem: filtros multi-coluna OR/AND, joins, pivots, "
        "agregações complexas, lambdas custom.\n\n"
        "AMBIENTE DISPONÍVEL no código:\n"
        "  • df          — o DataFrame corrente (pandas.DataFrame)\n"
        "  • pd          — pandas\n"
        "  • np          — numpy\n"
        "  • re          — regex\n"
        "  • rapidfuzz   — módulo rapidfuzz (se instalado)\n"
        "  • fuzz        — rapidfuzz.fuzz (token_sort_ratio, ratio, etc.)\n"
        "  • process     — rapidfuzz.process (extract, extractOne, etc.)\n"
        "  • difflib     — difflib stdlib (SequenceMatcher, get_close_matches)\n"
        "  • datasets    — dict {nome: DataFrame} com datasets salvos\n"
        "  • save_dataset(nome, df) — salva um DataFrame como dataset nomeado\n"
        "  • load_dataset(nome)     — carrega um dataset nomeado (retorna DataFrame)\n"
        "  • print(...)  — output capturado e devolvido a você\n"
        "  • result_df   — se atribuir, substitui o dataset da sessão\n\n"
        "MULTI-DATASET: use save_dataset('nome', df) para guardar datasets "
        "extras na sessão (ex.: lado A e lado B para matching). Depois "
        "acesse via datasets['nome'] ou load_dataset('nome'). O dataset "
        "corrente (df) não é afetado.\n\n"
        "POR DEFAULT é não-destrutivo: o dataset original NÃO muda. "
        "Para persistir uma transformação, atribua a result_df.\n\n"
        "RESTRIÇÕES (sandbox): sem import, open, exec, eval, dunders. "
        "Use os módulos já injetados.\n\n"
        "EXEMPLO — buscar termos em múltiplas colunas (OR):\n"
        "  mask = (\n"
        "    df['col_a'].str.contains('termo1', case=False, na=False)\n"
        "    | df['col_b'].str.contains('termo2', case=False, na=False)\n"
        "  )\n"
        "  print(df[mask].head(10).to_dict('records'))\n\n"
        "USE quando: precisar de lógica que não cabe em filtrar/agrupar/regex. "
        "NÃO use para: tarefas óbvias que outra tool já resolve em 1 chamada."
    ),
    icon="🐍",
)
def executar_pandas(codigo: str, _session: dict) -> str:
    """Executa código pandas sobre o dataset corrente.

    Args:
        codigo: Código Python a executar. Tem acesso a df, pd, np, re, rapidfuzz (fuzz, process), difflib, datasets (dict de DFs nomeados), save_dataset(nome, df), load_dataset(nome). NÃO use import — os módulos já estão injetados. Use print() para retornar saída. Atribua result_df para substituir o dataset corrente.
    """
    df = _get_df(_session)
    if df is None:
        return _err("Nenhum dataset na sessão. Carregue dados antes (ex: consulta_aws).")

    if not codigo or not codigo.strip():
        return _err("Código vazio.")

    motivo = _check_code_safety(codigo)
    if motivo:
        return _err(f"Código bloqueado pelo sandbox: {motivo}")

    import pandas as pd
    import numpy as np

    try:
        import rapidfuzz
        from rapidfuzz import process as _rfprocess, fuzz as _rffuzz
        _has_rapidfuzz = True
    except ImportError:
        _has_rapidfuzz = False

    try:
        import difflib as _difflib
        _has_difflib = True
    except ImportError:
        _has_difflib = False

    safe_builtins = {k: __builtins__[k] if isinstance(__builtins__, dict)
                     else getattr(__builtins__, k)
                     for k in _SAFE_BUILTINS
                     if (k in __builtins__ if isinstance(__builtins__, dict)
                         else hasattr(__builtins__, k))}

    named_dfs = _get_named_datasets(_session)

    def _sandbox_save_dataset(name: str, dataframe):
        _save_named_dataset(name, dataframe, _session)
        named_dfs[name] = dataframe

    def _sandbox_load_dataset(name: str):
        if name in named_dfs:
            return named_dfs[name]
        raise KeyError(f"Dataset '{name}' não encontrado. Disponíveis: {list(named_dfs.keys())}")

    namespace = {
        "__builtins__": safe_builtins,
        "df": df,
        "pd": pd,
        "np": np,
        "re": _re,
        "datasets": named_dfs,
        "save_dataset": _sandbox_save_dataset,
        "load_dataset": _sandbox_load_dataset,
    }

    if _has_rapidfuzz:
        namespace["rapidfuzz"] = rapidfuzz
        namespace["fuzz"] = _rffuzz
        namespace["process"] = _rfprocess

    if _has_difflib:
        namespace["difflib"] = _difflib

    stdout_buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(stdout_buffer):
            exec(codigo, namespace)
    except Exception as e:
        saida_parcial = stdout_buffer.getvalue()
        payload = {
            "erro": f"{type(e).__name__}: {e}",
            "saida_parcial": saida_parcial[-2000:] if saida_parcial else "",
        }
        return json.dumps(payload, ensure_ascii=False)

    saida = stdout_buffer.getvalue()

    # Se o agente atribuiu result_df, persiste no estado de sessão.
    persistido = False
    novo_shape = None
    if "result_df" in namespace and namespace["result_df"] is not None:
        result_df = namespace["result_df"]
        if isinstance(result_df, pd.DataFrame):
            _save_df(result_df, _session)
            persistido = True
            novo_shape = {"linhas": len(result_df), "colunas": len(result_df.columns)}
        else:
            return json.dumps({
                "erro": f"result_df deve ser DataFrame, recebido {type(result_df).__name__}",
                "saida": saida[-2000:],
            }, ensure_ascii=False)

    return json.dumps(
        {
            "ok": True,
            "saida": saida[-8000:] if saida else "(sem output — use print() para ver resultado)",
            "saida_truncada": len(saida) > 8000,
            "dataset_modificado": persistido,
            "novo_shape": novo_shape,
        },
        ensure_ascii=False,
        default=str,
    )
