"""
Tool de análise massiva com GenAI.

Processa cada linha do dataset com um LLM (em paralelo, 5 por vez),
criando colunas de classificação dinâmicas conforme pedido pelo usuário.
O modelo usado é configurável (default: gpt-4o-mini).
"""
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from uuid import uuid4

from .registry import tool


# ── Config ────────────────────────────────────────────────────────────

DEFAULT_MODEL_MASSIVA = os.environ.get("IARA_MODEL_MASSIVA", "gpt-4o-mini")
DEFAULT_MAX_WORKERS = 5   # fallback quando não há config no banco
WORKERS_HARD_CAP = 10     # teto absoluto — acima disso vira risco de rate limit
MAX_LINHAS = 8000


def _max_workers() -> int:
    """Lê o nº de workers paralelos da config global (com fallback seguro).

    Editável na tela de Configurações (Geral). Clampado entre 1 e o teto
    absoluto (WORKERS_HARD_CAP) para nunca extrapolar mesmo se o banco tiver
    um valor fora de faixa.
    """
    try:
        from auditor.models import AppSettings
        n = int(AppSettings.get_solo().massiva_workers)
    except Exception:
        return DEFAULT_MAX_WORKERS
    return max(1, min(WORKERS_HARD_CAP, n))


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


def _err(msg: str) -> str:
    return json.dumps({"erro": msg}, ensure_ascii=False)


def _build_prompt_for_row(texto: str, colunas_saida: list[str], contexto: str) -> str:
    schema_example = {col: "<valor>" for col in colunas_saida}
    return (
        f"Você é um classificador preciso. Analise o texto abaixo e retorne "
        f"APENAS um JSON com as chaves exatas: {json.dumps(colunas_saida, ensure_ascii=False)}.\n\n"
        f"Contexto/critério de análise:\n{contexto}\n\n"
        f"Texto a analisar:\n\"\"\"\n{texto}\n\"\"\"\n\n"
        f"Responda SOMENTE o JSON, sem markdown, sem explicação. Exemplo de formato:\n"
        f"{json.dumps(schema_example, ensure_ascii=False)}"
    )


def _call_llm_for_row(
    texto: str,
    colunas_saida: list[str],
    contexto: str,
    model: str,
    provider: str,
    row_index: int,
) -> dict:
    """Chama o LLM para uma única linha. Retorna dict com as colunas ou erro."""
    from iaragenai import IaraGenAI

    client = IaraGenAI(
        client_id=os.getenv("IARA_CLIENT_ID"),
        client_secret=os.getenv("IARA_CLIENT_SECRET"),
        environment=os.getenv("IARA_ENVIRONMENT", "homol"),
        provider=provider,
        correlation_id=str(uuid4()),
    )

    prompt = _build_prompt_for_row(texto, colunas_saida, contexto)

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
        )
        raw = response.choices[0].message.content.strip()

        # Remove possível markdown wrapper
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        result = json.loads(raw)

        # Garante que todas as colunas existem
        for col in colunas_saida:
            if col not in result:
                result[col] = None

        result["__index"] = row_index
        return result

    except json.JSONDecodeError:
        return {col: "[ERRO_PARSE]" for col in colunas_saida} | {"__index": row_index}
    except Exception as e:
        return {col: f"[ERRO: {str(e)[:80]}]" for col in colunas_saida} | {"__index": row_index}


def _provider_for(model: str) -> str:
    m = (model or "").lower()
    if "anthropic." in m or "claude" in m:
        return "bedrock"
    if m.startswith("gemini") or m.startswith("vertex"):
        return "vertex"
    if (m.startswith("gpt") or m.startswith("o1") or m.startswith("o3")
            or m.startswith("o4") or m.startswith("openai.")):
        return "azure_openai"
    return os.getenv("IARA_PROVIDER", "bedrock")


# ── Tool ──────────────────────────────────────────────────────────────

@tool(
    description=(
        "Análise massiva com GenAI: classifica cada linha do dataset "
        "usando um LLM, criando N colunas de saída definidas pelo "
        "usuário. Processamento paralelo (5 por vez). "
        "TETO TÉCNICO: 8.000 linhas — mas a quantidade dentro desse teto é "
        "decisão do USUÁRIO.\n"
        "⚠️ CONFIRMAÇÃO OBRIGATÓRIA: esta tool tem CUSTO (1 chamada de LLM por "
        "linha). Você SEMPRE precisa confirmar com o usuário ANTES de "
        "executar. Fluxo obrigatório:\n"
        "1. Use `ask_human` para mostrar ao usuário o plano (quantas linhas, "
        "qual coluna, quais colunas de saída, qual modelo) e PEDIR confirmação "
        "explícita.\n"
        "2. SÓ depois que o usuário confirmar, chame esta tool com "
        "`confirmado=true`.\n"
        "Se você chamar SEM `confirmado=true`, a execução é BLOQUEADA e nada "
        "roda — é uma trava de segurança, não um erro a contornar.\n"
        "QUANTAS LINHAS: passe 'limite' com o número que o usuário definiu "
        "('avalie 20 casos' → limite=20; 'processe tudo' → limite=0).\n"
        "PRÉ-REQUISITOS obrigatórios antes de chamar:\n"
        "1. descrever_dataset (para saber as colunas disponíveis)\n"
        "USE: quando o usuário pedir classificação, categorização ou "
        "validação em lote de registros textuais."
    ),
    icon="🚀",
)
def analise_massiva_llm(
    coluna_texto: str,
    colunas_saida: list[str],
    contexto: str,
    _session: dict,
    modelo: str = "",
    limite: int = 0,
    confirmado: bool = False,
) -> str:
    """Classifica cada linha do dataset com LLM, criando colunas dinâmicas.

    Args:
        coluna_texto: Nome da coluna que contém o texto a analisar.
        colunas_saida: Lista com os nomes das colunas que serão criadas (ex: ['Conformidade', 'Urgência', 'Justificativa']).
        contexto: Critério de análise ou texto da normativa/política que guia a classificação.
        modelo: Modelo LLM a usar (default: gpt-4o-mini). Configurável na env IARA_MODEL_MASSIVA.
        limite: Nº máximo de linhas a processar (as N primeiras). 0 = todas. Use quando o usuário pediu uma amostra (ex: 'avalie 20 casos' → limite=20).
        confirmado: Trava de segurança. Só passe True DEPOIS que o usuário confirmou explicitamente a execução (via ask_human). Se False, a execução é bloqueada e a tool apenas devolve o plano para você confirmar com o usuário.
    """
    df = _get_df(_session)
    if df is None:
        return _err("Nenhum dataset na sessão. Carregue dados antes.")

    if coluna_texto not in df.columns:
        return _err(f"Coluna '{coluna_texto}' não existe. Disponíveis: {list(df.columns)}")

    if not colunas_saida:
        return _err("'colunas_saida' vazio. Informe quais colunas criar (ex: ['Conformidade', 'Urgência']).")

    if not contexto or not contexto.strip():
        return _err("'contexto' vazio. Informe o critério/normativa para a classificação.")

    # ── Trava de confirmação (sempre antes de executar) ─────────────────
    #
    # A análise massiva tem custo real (1 chamada de LLM por linha). Mesmo
    # que o modelo "esqueça" de perguntar, esta trava impede a execução até
    # que `confirmado=True` seja passado — o que o agente só deve fazer após
    # o usuário aprovar explicitamente (via ask_human). Aqui devolvemos o
    # PLANO para o agente apresentar e pedir o OK.
    if not confirmado:
        total_dataset = len(df)
        linhas_alvo = min(limite, total_dataset) if (limite and limite > 0) else total_dataset
        model_preview = modelo.strip() if modelo and modelo.strip() else DEFAULT_MODEL_MASSIVA
        return json.dumps({
            "confirmacao_necessaria": True,
            "executou": False,
            "plano": {
                "linhas_a_processar": linhas_alvo,
                "total_no_dataset": total_dataset,
                "coluna_texto": coluna_texto,
                "colunas_saida": colunas_saida,
                "modelo": model_preview,
                "custo_estimado": f"{linhas_alvo} chamadas de LLM",
            },
            "instrucao": (
                "NÃO execute ainda. Mostre este plano ao usuário com ask_human "
                "e peça confirmação explícita. Só então rode novamente esta "
                "tool com confirmado=true."
            ),
        }, ensure_ascii=False)

    # Amostra: se 'limite' > 0, processa só as N primeiras linhas. Evita
    # rodar a análise no dataset inteiro quando o usuário pediu amostra.
    total_dataset = len(df)
    if limite and limite > 0:
        df = df.head(limite).copy()

    total = len(df)
    if total > MAX_LINHAS:
        return _err(
            f"Vai processar {total} linhas — excede o limite de {MAX_LINHAS}. "
            f"Passe 'limite' (ex: limite=20) ou filtre o dataset antes."
        )

    model = modelo.strip() if modelo and modelo.strip() else DEFAULT_MODEL_MASSIVA
    provider = _provider_for(model)

    # Callback de progresso ao vivo (SSE), injetado pelo run_agent na sessão.
    progress = _session.get("__progress")

    def _emit_progress(feitos: int) -> None:
        if progress is None:
            return
        try:
            progress({
                "stage": "massiva",
                "text": f"Análise massiva por IA — {feitos} de {total}",
                "current": feitos,
                "total": total,
            })
        except Exception:
            pass

    # Processa em paralelo
    start = time.perf_counter()
    results: list[dict] = []
    erros = 0

    max_workers = _max_workers()
    _emit_progress(0)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {}
        for idx, row in df.iterrows():
            texto = str(row.get(coluna_texto, "") or "")
            if not texto.strip():
                # Linha vazia: preenche com N/A
                results.append(
                    {col: "N/A" for col in colunas_saida} | {"__index": idx}
                )
                continue

            future = executor.submit(
                _call_llm_for_row,
                texto=texto,
                colunas_saida=colunas_saida,
                contexto=contexto,
                model=model,
                provider=provider,
                row_index=idx,
            )
            futures[future] = idx

        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            if any("[ERRO" in str(v) for v in result.values()):
                erros += 1
            # Atualiza o log a cada linha concluída.
            _emit_progress(len(results))

    # Monta as novas colunas no DataFrame
    for col in colunas_saida:
        df[col] = None

    for r in results:
        idx = r.pop("__index")
        for col in colunas_saida:
            df.at[idx, col] = r.get(col)

    _save_df(df, _session)
    elapsed = round(time.perf_counter() - start, 1)

    payload = {
        "ok": True,
        "linhas_processadas": total,
        "colunas_criadas": colunas_saida,
        "modelo_usado": model,
        "erros": erros,
        "tempo_segundos": elapsed,
        "amostra": df[colunas_saida + [coluna_texto]].head(5).to_dict(orient="records"),
    }
    # Deixa explícito quando foi amostra (vs dataset inteiro). O dataset em
    # sessão agora contém SÓ as linhas processadas.
    if limite and limite > 0 and limite < total_dataset:
        payload["amostra_de"] = total_dataset
        payload["nota"] = (
            f"Processadas {total} linhas (amostra) de {total_dataset} no dataset. "
            f"O dataset em sessão agora contém apenas essas {total} linhas classificadas."
        )

    return json.dumps(payload, ensure_ascii=False, default=str)
