"""
Tool de análise massiva via **Batch API** do IARA.

Alternativa ao `analise_massiva_llm` (ThreadPool síncrono): em vez de disparar
N chamadas de LLM presas à conexão do processo, monta um único arquivo JSONL
(1 linha por linha do dataset), envia como um job de batch e deixa o IARA
processar do lado dele. O job sobrevive a queda de internet / restart do
processo — o que importa é o `job_id`, que é persistido no banco (BatchJob).

Fluxo:
    1. create()            → job_id + presigned_url (upload PUT, válida ~15s)
    2. upload do JSONL      → job vai de PENDING → PREPROCESSING sozinho
    3. persistir job_id     → BatchJob no banco (sobrevive a restart)
    4. poll get_status()    → até estado terminal (COMPLETED/FAILED/CANCELLED)
    5. download + merge     → casa cada resposta de volta na linha (custom_id)

Quando usar batch vs. o massivo síncrono:
    - batch  : volume alto, ninguém esperando na tela, custo/robustez importam.
    - síncrono (analise_massiva_llm): amostras, quer ver rolando ao vivo.

FORMATO DO JSONL (confirmado na doc "Exemplos e Padrões" do IARA): linha PLANA
— custom_id + model + messages no topo (NÃO o envelope aninhado method/url/body
do OpenAI). Ver `_build_jsonl_line`.

⚠️ Modelos de raciocínio (GPT-5, o-series) REJEITAM temperature≠1 e max_tokens
(exigem max_completion_tokens). O massivo síncrono manda temperature=0.1 — em
batch isso derruba o job na validação. `_sampling_params` trata isso.

⚠️ Batch só existe em dev/homol (não em prod) e resultados ficam retidos 7 dias.
"""
import json
import os
import time
from uuid import uuid4

from .registry import tool
from .analise_massiva import (
    _get_df,
    _save_df,
    _err,
    _build_prompt_for_row,
    _provider_for,
    DEFAULT_MODEL_MASSIVA,
    MAX_LINHAS,
)


# ── Config ────────────────────────────────────────────────────────────

# Sufixo obrigatório dos modelos de batch no IARA (ex: "gpt-4.1-batch").
BATCH_MODEL_SUFFIX = "-batch"

# Polling: intervalo entre get_status() e teto de tempo do caminho "feliz"
# (poll automático). Se estourar, o job segue vivo no IARA e é recuperável
# depois via buscar_resultado_batch(job_id) — o banco tem o job_id.
POLL_INTERVAL_S = int(os.environ.get("IARA_BATCH_POLL_INTERVAL_S", "10"))
POLL_TIMEOUT_S = int(os.environ.get("IARA_BATCH_POLL_TIMEOUT_S", "1800"))  # 30 min

# Custom_id não pode colidir; prefixo + índice da linha (posição no df).
CUSTOM_ID_PREFIX = "row-"


# ── Cliente / SDK ───────────────────────────────────────────────────────

def _batch_model(model: str) -> str:
    """Garante o sufixo -batch exigido pelo IARA para jobs de batch."""
    m = (model or DEFAULT_MODEL_MASSIVA).strip()
    return m if m.endswith(BATCH_MODEL_SUFFIX) else m + BATCH_MODEL_SUFFIX


def _presigned_env():
    """Ambiente da URL pré-assinada: CLOUD em devops/consumer, LOCAL local.

    Controlado por IARA_BATCH_PRESIGNED_ENV (default CLOUD, pois em produção
    o código roda em conta devops/consumer — na máquina local basta exportar
    IARA_BATCH_PRESIGNED_ENV=LOCAL).
    """
    from iaragenai import BatchPresignedUrlEnvironment
    val = os.getenv("IARA_BATCH_PRESIGNED_ENV", "CLOUD").upper()
    return (BatchPresignedUrlEnvironment.LOCAL if val == "LOCAL"
            else BatchPresignedUrlEnvironment.CLOUD)


def _make_client():
    from iaragenai import IaraGenAI
    return IaraGenAI(
        client_id=os.getenv("IARA_CLIENT_ID"),
        client_secret=os.getenv("IARA_CLIENT_SECRET"),
        environment=os.getenv("IARA_ENVIRONMENT", "homol"),
        provider=os.getenv("IARA_PROVIDER", "azure_openai"),
        correlation_id=str(uuid4()),
    )


# ── Funções puras (testáveis sem IARA) ───────────────────────────────────

def _is_reasoning_model(model: str) -> bool:
    """GPT-5 e o-series: rejeitam temperature≠1 e max_tokens (regra do IARA)."""
    m = (model or "").lower().replace("openai.", "")
    return (m.startswith("gpt-5") or m.startswith("o1")
            or m.startswith("o3") or m.startswith("o4"))


def _sampling_params(model: str) -> dict:
    """Parâmetros de sampling válidos p/ o modelo no JSONL de batch.

    Modelos de raciocínio (GPT-5/o-series) só aceitam temperature=1 (default,
    então omitimos) e não aceitam max_tokens. Demais modelos podem usar o
    temperature=0.1 baixo do massivo (classificação determinística).
    """
    if _is_reasoning_model(model):
        return {}
    return {"temperature": 0.1}


def _build_jsonl_line(custom_id: str, texto: str, colunas_saida: list,
                      contexto: str, model: str) -> dict:
    """Monta UMA linha do JSONL — formato PLANO do IARA (custom_id/model/messages
    no topo), conforme a doc "Exemplos e Padrões". NÃO usa o envelope aninhado
    method/url/body do OpenAI.
    """
    return {
        "custom_id": custom_id,
        "model": model,
        "messages": [
            {"role": "user",
             "content": _build_prompt_for_row(texto, colunas_saida, contexto)}
        ],
        **_sampling_params(model),
    }


def build_jsonl(df, coluna_texto: str, colunas_saida: list, contexto: str,
                model: str) -> tuple:
    """Constrói o corpo JSONL (str) + o mapa custom_id→índice do df.

    Linhas com texto vazio NÃO viram requisição (economia): entram no mapa de
    "vazias" para serem preenchidas com N/A no merge, sem gastar chamada.

    Retorna: (jsonl_str, id_to_index: dict, vazias: dict[idx→'N/A']).
    """
    linhas = []
    id_to_index = {}
    vazias = {}
    for pos, (idx, row) in enumerate(df.iterrows()):
        texto = str(row.get(coluna_texto, "") or "")
        if not texto.strip():
            vazias[idx] = "N/A"
            continue
        cid = f"{CUSTOM_ID_PREFIX}{pos}"
        id_to_index[cid] = idx
        linhas.append(_build_jsonl_line(cid, texto, colunas_saida, contexto, model))
    jsonl = "\n".join(json.dumps(l, ensure_ascii=False) for l in linhas)
    return jsonl, id_to_index, vazias


def _extract_content(obj: dict):
    """Extrai o texto da resposta de UMA linha de resultado, tolerante a formato.

    A doc do IARA fixa o shape da linha de ERRO (custom_id + error.message), mas
    não detalha o aninhamento do sucesso. Tentamos os caminhos plausíveis:
      - OpenAI batch: response.body.choices[0].message.content
      - achatado:     body.choices[0].message.content
      - direto:       choices[0].message.content
    Retorna a string de conteúdo, ou None se não achar (vira [ERRO_RESPOSTA]).
    """
    candidatos = [
        obj.get("response", {}).get("body", {}) if isinstance(obj.get("response"), dict) else {},
        obj.get("body", {}) if isinstance(obj.get("body"), dict) else {},
        obj,
    ]
    for c in candidatos:
        try:
            content = c["choices"][0]["message"]["content"]
            if content is not None:
                return content
        except (KeyError, IndexError, TypeError):
            continue
    return None


def _parse_result_content(raw: str, colunas_saida: list) -> dict:
    """Parseia o conteúdo textual de UMA resposta no dict de colunas.

    Mesma tolerância do massivo síncrono: remove cerca markdown, backfill de
    chaves ausentes, sentinelas em erro de parse.
    """
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        return {col: "[ERRO_PARSE]" for col in colunas_saida}
    return {col: result.get(col) for col in colunas_saida}


def merge_results(df, results_jsonl: str, id_to_index: dict, vazias: dict,
                  colunas_saida: list) -> tuple:
    """Casa o JSONL de resultados de volta no df pelas custom_id.

    Cada linha de `results_jsonl` traz custom_id + a resposta do provider.
    Retorna (df com colunas preenchidas, n_erros).

    Robusto a: linhas de resultado fora de ordem, custom_id desconhecido
    (ignorado), e linhas do df sem resposta (ficam com sentinela de falta).
    """
    for col in colunas_saida:
        df[col] = None

    # Linhas de texto vazio: preenchidas sem gastar chamada.
    for idx, val in vazias.items():
        for col in colunas_saida:
            df.at[idx, col] = val

    erros = 0
    respondidos = set()
    for line in (results_jsonl or "").splitlines():
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        cid = obj.get("custom_id")
        idx = id_to_index.get(cid)
        if idx is None:
            continue  # custom_id que não é nosso — ignora
        respondidos.add(cid)
        raw = _extract_content(obj)
        if raw is None:
            for col in colunas_saida:
                df.at[idx, col] = "[ERRO_RESPOSTA]"
            erros += 1
            continue
        parsed = _parse_result_content(raw, colunas_saida)
        if any(v == "[ERRO_PARSE]" for v in parsed.values()):
            erros += 1
        for col in colunas_saida:
            df.at[idx, col] = parsed.get(col)

    # Linhas que pedimos mas não voltaram (podem estar no arquivo de erros).
    faltando = set(id_to_index) - respondidos
    for cid in faltando:
        idx = id_to_index[cid]
        for col in colunas_saida:
            df.at[idx, col] = "[SEM_RESPOSTA]"
        erros += 1

    return df, erros


# ── I/O de rede (mockável) ────────────────────────────────────────────────

def _http_put(url: str, data: bytes) -> None:
    """Upload do JSONL na presigned_url (PUT). Válida ~15s — chamar já."""
    import urllib.request
    req = urllib.request.Request(url, data=data, method="PUT")
    req.add_header("Content-Type", "application/octet-stream")
    urllib.request.urlopen(req, timeout=30)


def _http_get(url: str) -> str:
    """Download de resultados/erros (GET). URL válida ~15s após get_status()."""
    import urllib.request
    with urllib.request.urlopen(url, timeout=60) as resp:
        return resp.read().decode("utf-8")


# ── Persistência do job (banco) ──────────────────────────────────────────

def _save_job(job_id: str, meta: dict) -> None:
    """Persiste o job no banco para sobreviver a restart / recuperação.

    Best-effort: se o model/banco não estiver disponível, não derruba o fluxo
    (o poll automático ainda funciona nesta execução).
    """
    try:
        from auditor.models import BatchJob
        BatchJob.objects.update_or_create(
            job_id=job_id, defaults={"meta": meta, "status": meta.get("status", "PENDING")}
        )
    except Exception:
        pass


def _load_job(job_id: str) -> dict:
    try:
        from auditor.models import BatchJob
        obj = BatchJob.objects.filter(job_id=job_id).first()
        return obj.meta if obj else {}
    except Exception:
        return {}


def _update_job_status(job_id: str, status: str) -> None:
    try:
        from auditor.models import BatchJob
        BatchJob.objects.filter(job_id=job_id).update(status=status)
    except Exception:
        pass


# ── Tool: dispara + poll + merge ──────────────────────────────────────────

@tool(
    description=(
        "Análise massiva via BATCH API do IARA (alternativa robusta ao "
        "analise_massiva_llm síncrono). Monta 1 arquivo JSONL com 1 requisição "
        "por linha do dataset e o processa como um job no servidor do IARA — "
        "NÃO fica preso à conexão: o job sobrevive a queda de internet e "
        "restart (o job_id é salvo no banco). Mais barato e maior throughput "
        "que o ThreadPool, ao custo de MAIOR LATÊNCIA (não entrega na hora).\n"
        "USE quando: volume alto (regra de bolso: > 500 linhas), ninguém "
        "esperando resposta imediata na tela.\n"
        "TETO: 8.000 linhas.\n"
        "⚠️ CONFIRMAÇÃO OBRIGATÓRIA (mesma trava do massivo): esta tool tem "
        "CUSTO (1 requisição de LLM por linha). Fluxo:\n"
        "1. ask_human mostrando o plano (linhas, coluna, colunas de saída, "
        "modelo, MODO=batch) e pedindo OK explícito.\n"
        "2. Só então chame com confirmado=true.\n"
        "Sem confirmado=true a execução é BLOQUEADA (trava de segurança).\n"
        "Ao final do poll devolve o job_id — se o poll expirar antes de "
        "COMPLETED, o job segue vivo e o resultado é recuperável com "
        "buscar_resultado_batch(job_id)."
    ),
    icon="📦",
)
def analise_massiva_batch(
    coluna_texto: str,
    colunas_saida: list,
    contexto: str,
    _session: dict,
    modelo: str = "",
    limite: int = 0,
    confirmado: bool = False,
) -> str:
    """Classifica cada linha do dataset via Batch API, criando colunas dinâmicas.

    Args:
        coluna_texto: Nome da coluna que contém o texto a analisar.
        colunas_saida: Nomes das colunas a criar (ex: ['Conformidade', 'Urgência']).
        contexto: Critério de análise ou texto da normativa que guia a classificação.
        modelo: Modelo LLM (o sufixo -batch é adicionado automaticamente). Default: gpt-4o-mini.
        limite: Nº máximo de linhas (as N primeiras). 0 = todas.
        confirmado: Trava de segurança. Só True DEPOIS de o usuário aprovar via ask_human.
    """
    df = _get_df(_session)
    if df is None:
        return _err("Nenhum dataset na sessão. Carregue dados antes.")
    if coluna_texto not in df.columns:
        return _err(f"Coluna '{coluna_texto}' não existe. Disponíveis: {list(df.columns)}")
    if not colunas_saida:
        return _err("'colunas_saida' vazio. Informe quais colunas criar.")
    if not contexto or not contexto.strip():
        return _err("'contexto' vazio. Informe o critério/normativa.")

    total_dataset = len(df)
    linhas_alvo = min(limite, total_dataset) if (limite and limite > 0) else total_dataset
    model = _batch_model(modelo)

    # ── Trava de confirmação (mesma do massivo síncrono) ────────────────
    if not confirmado:
        return json.dumps({
            "confirmacao_necessaria": True,
            "executou": False,
            "plano": {
                "modo": "batch",
                "linhas_a_processar": linhas_alvo,
                "total_no_dataset": total_dataset,
                "coluna_texto": coluna_texto,
                "colunas_saida": colunas_saida,
                "modelo": model,
                "custo_estimado": f"{linhas_alvo} requisições de LLM (em lote)",
                "nota": ("Modo batch: mais barato e robusto a queda de conexão, "
                         "mas NÃO entrega na hora — o job roda no servidor."),
            },
            "instrucao": ("NÃO execute ainda. Mostre este plano ao usuário com "
                          "ask_human e peça confirmação. Só então rode com "
                          "confirmado=true."),
        }, ensure_ascii=False)

    if limite and limite > 0:
        df = df.head(limite).copy()
    total = len(df)
    if total > MAX_LINHAS:
        return _err(f"Vai processar {total} linhas — excede o limite de {MAX_LINHAS}. "
                    f"Passe 'limite' ou filtre o dataset antes.")

    # ── Monta o JSONL ────────────────────────────────────────────────────
    jsonl, id_to_index, vazias = build_jsonl(df, coluna_texto, colunas_saida, contexto, model)
    if not id_to_index:
        # Tudo vazio: nem cria job, só preenche N/A.
        df, _ = merge_results(df, "", id_to_index, vazias, colunas_saida)
        _save_df(df, _session)
        return json.dumps({"ok": True, "linhas_processadas": total, "erros": 0,
                           "nota": "Todas as linhas tinham texto vazio (N/A), nenhum job criado."},
                          ensure_ascii=False)

    start = time.perf_counter()
    progress = _session.get("__progress")

    def _emit(text, current=0):
        if progress:
            try:
                progress({"stage": "massiva_batch", "text": text,
                          "current": current, "total": total})
            except Exception:
                pass

    # ── create → upload → persistir ──────────────────────────────────────
    try:
        from iaragenai.exceptions import BatchSubmissionError
    except Exception:
        BatchSubmissionError = Exception

    # A doc do IARA insiste em fechar o cliente (with/close). Como o cliente é
    # usado em create → poll → finalize, fechamos num finally que cobre tudo.
    client = _make_client()
    presigned_env = _presigned_env()
    try:
        try:
            _emit("Criando job de batch…")
            batch = client.batch_jobs.create(
                provider=os.getenv("IARA_PROVIDER", "azure_openai"),
                model=model,
                presigned_url_environment=presigned_env,
            )
            job_id = batch.job_id
            # A presigned de upload expira em ~15s: sobe JÁ, sem I/O no meio.
            _http_put(batch.presigned_url, jsonl.encode("utf-8"))
        except BatchSubmissionError as e:
            return _err(f"Falha ao criar/enviar job de batch: {e}")
        except Exception as e:
            return _err(f"Erro inesperado ao submeter batch: {e}")

        # Persistência do job_id ANTES de qualquer poll: se o processo cair
        # agora, o job segue vivo no IARA e é recuperável por
        # buscar_resultado_batch.
        job_meta = {
            "job_id": job_id, "status": "PENDING", "modelo": model,
            "coluna_texto": coluna_texto, "colunas_saida": colunas_saida,
            "id_to_index": id_to_index,
            "vazias": {str(k): v for k, v in vazias.items()},
            "total": total,
            "presigned_env": os.getenv("IARA_BATCH_PRESIGNED_ENV", "CLOUD"),
        }
        _save_job(job_id, job_meta)
        _emit(f"Job {job_id} enviado. Processando no servidor…")

        # ── Poll automático (caminho feliz) ──────────────────────────────
        status = _poll_until_terminal(client, job_id, presigned_env, _emit, total)

        if status is None:
            # Timeout do poll: job segue vivo. Devolve job_id p/ recuperar.
            return json.dumps({
                "ok": False, "pendente": True, "job_id": job_id,
                "status": "em_processamento",
                "nota": (f"O job {job_id} ainda está processando (poll expirou "
                         f"em {POLL_TIMEOUT_S}s). Ele NÃO foi cancelado — "
                         f"continua no servidor. Recupere o resultado depois "
                         f"com buscar_resultado_batch(job_id='{job_id}')."),
            }, ensure_ascii=False)

        return _finalize(client, job_id, status, presigned_env, _session,
                         df, id_to_index, vazias, colunas_saida, coluna_texto,
                         total, total_dataset, limite, start)
    finally:
        try:
            client.close()
        except Exception:
            pass


def _poll_until_terminal(client, job_id, presigned_env, emit, total):
    """Chama get_status em loop até estado terminal ou timeout.

    Retorna o objeto de status terminal, ou None se estourar o teto de tempo
    (job continua vivo no servidor — recuperável pelo job_id).
    """
    TERMINAIS = {"COMPLETED", "FAILED", "CANCELLED"}
    waited = 0
    while waited <= POLL_TIMEOUT_S:
        status = client.batch_jobs.get_status(job_id, presigned_url_environment=presigned_env)
        st = getattr(status.status, "value", str(status.status)).upper()
        done = getattr(status, "success_count", 0) or 0
        emit(f"Batch: {done}/{total} processadas (status {st})", current=done)
        _update_job_status(job_id, st)
        if st in TERMINAIS:
            return status
        time.sleep(POLL_INTERVAL_S)
        waited += POLL_INTERVAL_S
    return None


def _finalize(client, job_id, status, presigned_env, _session, df,
              id_to_index, vazias, colunas_saida, coluna_texto,
              total, total_dataset, limite, start):
    """Baixa resultados de um status terminal e casa de volta no df."""
    st = getattr(status.status, "value", str(status.status)).upper()

    if st == "FAILED":
        return _err(f"Job {job_id} FALHOU: {getattr(status, 'status_detail', '') or 'erro crítico'}")
    if st == "CANCELLED":
        return json.dumps({"ok": False, "cancelado": True, "job_id": job_id,
                           "nota": "Job cancelado. Linhas já processadas não são revertidas."},
                          ensure_ascii=False)

    # COMPLETED — pode ter erros parciais.
    results_jsonl = ""
    if getattr(status, "presigned_url", None):
        results_jsonl = _http_get(status.presigned_url)

    df, erros = merge_results(df, results_jsonl, id_to_index, vazias, colunas_saida)
    _save_df(df, _session)
    elapsed = round(time.perf_counter() - start, 1)

    payload = {
        "ok": True, "modo": "batch", "job_id": job_id,
        "linhas_processadas": total,
        "colunas_criadas": colunas_saida,
        "erros": erros,
        "erros_reportados_pelo_provider": getattr(status, "error_count", 0),
        "tempo_segundos": elapsed,
        "amostra": df[colunas_saida + [coluna_texto]].head(5).to_dict(orient="records"),
    }
    if getattr(status, "error_presigned_url", None):
        payload["nota_erros"] = ("Há erros individuais — arquivo de erros disponível "
                                 "(error_presigned_url). Verifique error_count.")
    if limite and limite > 0 and limite < total_dataset:
        payload["amostra_de"] = total_dataset
        payload["nota"] = (f"Processadas {total} linhas (amostra) de {total_dataset}. "
                           f"O dataset em sessão agora contém apenas essas {total}.")
    return json.dumps(payload, ensure_ascii=False, default=str)


# ── Tool: recupera resultado de um job pendente ───────────────────────────

@tool(
    description=(
        "Recupera o resultado de um job de análise massiva em BATCH criado "
        "antes (por analise_massiva_batch) cujo poll expirou ou cuja sessão foi "
        "fechada. Consulta o status atual do job pelo job_id; se já estiver "
        "COMPLETED, baixa os resultados e preenche as colunas no dataset da "
        "sessão — exatamente como o massivo entrega. Se ainda estiver "
        "processando, apenas informa o status (o job continua vivo no servidor)."
    ),
    icon="📥",
)
def buscar_resultado_batch(job_id: str, _session: dict) -> str:
    """Consulta e, se pronto, baixa/mescla o resultado de um job de batch.

    Args:
        job_id: Identificador do job retornado por analise_massiva_batch.
    """
    meta = _load_job(job_id)
    if not meta:
        return _err(f"Job '{job_id}' não encontrado no registro. Confira o job_id.")

    df = _get_df(_session)
    if df is None:
        return _err("Nenhum dataset na sessão para receber os resultados. Carregue os dados.")

    coluna_texto = meta.get("coluna_texto")
    colunas_saida = meta.get("colunas_saida", [])
    id_to_index = {k: int(v) for k, v in meta.get("id_to_index", {}).items()}
    vazias = {int(k): v for k, v in meta.get("vazias", {}).items()}

    from iaragenai.exceptions import BatchNotFoundError
    presigned_env = _presigned_env()
    client = _make_client()
    try:
        try:
            status = client.batch_jobs.get_status(job_id, presigned_url_environment=presigned_env)
        except BatchNotFoundError:
            return _err(f"Job '{job_id}' não existe mais no IARA (404).")

        st = getattr(status.status, "value", str(status.status)).upper()
        _update_job_status(job_id, st)

        if st not in {"COMPLETED", "FAILED", "CANCELLED"}:
            done = getattr(status, "success_count", 0)
            return json.dumps({"ok": False, "pendente": True, "job_id": job_id, "status": st,
                               "progresso": f"{done}/{meta.get('total', '?')}",
                               "nota": "Ainda processando. Tente novamente em instantes."},
                              ensure_ascii=False)

        return _finalize(client, job_id, status, presigned_env, _session, df,
                         id_to_index, vazias, colunas_saida, coluna_texto,
                         meta.get("total", len(df)), meta.get("total", len(df)), 0,
                         time.perf_counter())
    finally:
        try:
            client.close()
        except Exception:
            pass
