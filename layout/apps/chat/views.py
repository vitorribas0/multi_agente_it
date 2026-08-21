import json
import mimetypes
import os
import re
import inspect
import sys
import uuid
from pathlib import Path

from django.conf import settings
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

# Adiciona os caminhos dos servidores MCP ao sys.path para importar as tools
_mcp_reclamacao_path = str(settings.BASE_DIR / "src" / "mcp_reclamacao")
_mcp_anomalias_path = str(settings.BASE_DIR / "src" / "mcp_anomalias")

# Importa o servidor de reclamação usando importlib para evitar conflitos de nome 'server'
import importlib.util

def load_module_from_path(name, path):
    spec = importlib.util.spec_from_file_location(name, os.path.join(path, "server.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module

server_reclamacao = load_module_from_path("server_reclamacao", _mcp_reclamacao_path)
server_anomalias = load_module_from_path("server_anomalias", _mcp_anomalias_path)

from iaragenai import IaraGenAI

from .agent_config import (
    AGENTS, DEFAULT_MODEL, MODEL_OPTIONS, SYSTEM_PROMPT, TOOLS, TOOLS_ANOMALIAS,
    PROMPT_AGENTE_ETL, PROMPT_AGENTE_FEATURES, PROMPT_AGENTE_DETECCAO,
    MCP_SERVERS, CONVERSATIONAL_PROMPT,
)

# Mapa de tools por agente (para agentes com tools próprias)
# Preenchido após definir os subsets abaixo
_AGENT_TOOLS_MAP: dict = {}

# Configuração do novo cliente IaraGenAI
_client = IaraGenAI(
    client_id=os.environ.get("IARA_CLIENT_ID"),
    client_secret=os.environ.get("IARA_CLIENT_SECRET"),
    environment=os.environ.get("IARA_ENVIRONMENT", "homol"),
    provider=os.environ.get("IARA_PROVIDER", "azure_openai"),
    correlation_id=str(uuid.uuid4()),
)

# ── Bridge: transfere dados do cache do mcp_reclamacao para o mcp_anomalias ──────
import pandas as pd

def importar_dados_reclamacao(dataset_id: str = "reclamacoes", usar_filtrado: bool = True) -> str:
    """
    Importa os dados já carregados pelo mcp_reclamacao (via query ou arquivo)
    diretamente para o cache do mcp_anomalias, sem precisar passar nenhum arquivo.

    Args:
        dataset_id: Nome para identificar o dataset no mcp_anomalias (padrão 'reclamacoes').
        usar_filtrado: Se True (padrão), importa o cache filtrado (resultado do último filtro aplicado).
                       Se False, importa o dataset completo.

    Returns:
        Resumo do dataset importado.
    """
    try:
        # Tenta ler o cache do mcp_reclamacao
        cache_rec = server_reclamacao._CACHE_FILTRADO if usar_filtrado else server_reclamacao._CACHE

        if not cache_rec:
            # Fallback: tenta o cache completo se o filtrado estiver vazio
            cache_rec = server_reclamacao._CACHE

        if not cache_rec:
            return json.dumps({
                "erro": "Nenhum dado encontrado no cache do mcp_reclamacao. "
                        "Use o agente de reclamações (@mcp_reclamacao) primeiro para extrair ou carregar dados."
            }, ensure_ascii=False)

        # Pega o dataset mais recente (último caminho inserido no cache)
        ultimo_caminho = list(cache_rec.keys())[-1]
        registros = cache_rec[ultimo_caminho]

        if not registros:
            return json.dumps({"erro": "Cache encontrado mas sem registros."}, ensure_ascii=False)

        # Carrega no cache do mcp_anomalias
        df = pd.DataFrame(registros)
        server_anomalias._cache[dataset_id] = df

        return json.dumps({
            "status": f"✅ {len(registros)} registros importados do mcp_reclamacao para o mcp_anomalias",
            "dataset_id": dataset_id,
            "shape": {"rows": df.shape[0], "columns": df.shape[1]},
            "columns": df.columns.tolist(),
            "origem": "filtrado" if usar_filtrado else "completo",
            "caminho_origem": ultimo_caminho,
        }, ensure_ascii=False)

    except Exception as e:
        return json.dumps({"erro": f"Falha ao importar dados: {e}"}, ensure_ascii=False)


# Mapeamento unificado de todas as tools disponíveis nos servidores
TOOL_MAP = {
    # Tools Reclamação
    "carregar_arquivo": server_reclamacao.carregar_arquivo,
    "query_fq_database": server_reclamacao.query_fq_database,
    "filtrar_registros": server_reclamacao.filtrar_registros,
    "exportar_dataframe": server_reclamacao.exportar_dataframe,
    "normalizar_nlp": server_reclamacao.normalizar_nlp,
    "lematizar_nlp": server_reclamacao.lematizar_nlp,
    "filtrar_por_palavras": server_reclamacao.filtrar_por_palavras,
    "analisar_serie_temporal": server_reclamacao.analisar_serie_temporal,
    "analise_massiva_llm": server_reclamacao.analise_massiva_llm,
    
    # Tools Anomalias
    "importar_dados_reclamacao": importar_dados_reclamacao,
    "load_data": server_anomalias.load_data,
    "load_dataframe_from_json": server_anomalias.load_dataframe_from_json,
    "list_datasets": server_anomalias.list_datasets,
    "preview_data": server_anomalias.preview_data,
    "assess_data_quality": server_anomalias.assess_data_quality,
    "auto_clean_data": server_anomalias.auto_clean_data,
    "suggest_features": server_anomalias.suggest_features,
    "apply_high_confidence_features": server_anomalias.apply_high_confidence_features,
    "analyze_for_detection": server_anomalias.analyze_for_detection,
    "detect_anomalies": server_anomalias.detect_anomalies,
    "run_full_pipeline": server_anomalias.run_full_pipeline,
    "get_insights": server_anomalias.get_insights,
    "explain_anomaly": server_anomalias.explain_anomaly,
    "export_report": server_anomalias.export_report,
    "export_anomalies_csv": server_anomalias.export_anomalies_csv,
    "execute_python": server_anomalias.execute_python,
}

# Subsets de tools por agente especializado do MCP Anomalias
_ETL_TOOL_NAMES = {"importar_dados_reclamacao", "load_data", "load_dataframe_from_json", "list_datasets", "preview_data", "assess_data_quality", "auto_clean_data"}
_FEATURES_TOOL_NAMES = {"suggest_features", "apply_high_confidence_features", "analyze_for_detection", "execute_python"}
_DETECCAO_TOOL_NAMES = {"detect_anomalies", "run_full_pipeline", "get_insights", "explain_anomaly", "export_anomalies_csv", "export_report", "execute_python"}

# Preenche o mapa de tools por agente (depende de TOOLS_ANOMALIAS)
_AGENT_TOOLS_MAP = {
    "orquestrador_anomalias": TOOLS_ANOMALIAS,
    "agente_etl": [t for t in TOOLS_ANOMALIAS if t["function"]["name"] in _ETL_TOOL_NAMES],
    "agente_features": [t for t in TOOLS_ANOMALIAS if t["function"]["name"] in _FEATURES_TOOL_NAMES],
    "agente_deteccao": [t for t in TOOLS_ANOMALIAS if t["function"]["name"] in _DETECCAO_TOOL_NAMES],
}


def _tool_flow_summary(tool_name: str) -> list[str]:
    """Resumo didático de funcionamento para exibição na UI."""
    summaries = {
        "importar_dados_reclamacao": [
            "Lê o cache interno do mcp_reclamacao (filtrado ou completo)",
            "Converte os registros em DataFrame pandas",
            "Carrega diretamente no cache do mcp_anomalias com o dataset_id informado",
            "Nenhum arquivo ou JSON precisa ser passado — a ponte é automática",
        ],
        "carregar_arquivo": [
            "Valida existência e extensão do arquivo (.csv/.xlsx/.xls)",
            "Lê os dados com pandas",
            "Armazena registros completos em cache interno",
            "Retorna colunas e preview de 3 linhas",
        ],
        "query_fq_database": [
            "Configura proxies internos do Itaú para conexão AWS",
            "Executa SQL no Athena via awswrangler",
            "Aplica LIMIT 20 por padrão para segurança",
            "Converte resposta para JSON estruturado",
        ],
        "filtrar_registros": [
            "Lê o dataframe do cache",
            "Converte filtros_json para lista de filtros",
            "Aplica filtros por coluna e operador",
            "Salva resultado no cache filtrado e retorna resumo",
        ],
        "normalizar_nlp": [
            "Lê dataframe do cache",
            "Normaliza texto (lowercase, sem acento, sem pontuação)",
            "Cria coluna com sufixo _limpo",
            "Atualiza cache e retorna confirmação",
        ],
        "lematizar_nlp": [
            "Lê dataframe do cache",
            "Carrega modelo spaCy pt_core_news_sm",
            "Lematiza a coluna original ou coluna _limpo",
            "Cria coluna com sufixo _lemma e atualiza cache",
        ],
        "filtrar_por_palavras": [
            "Lê dataframe do cache",
            "Monta padrão com palavras-chave",
            "Filtra linhas da coluna alvo por correspondência",
            "Salva resultado no cache filtrado",
        ],
        "exportar_dataframe": [
            "Escolhe origem: filtrado (padrão) ou completo",
            "Converte registros em DataFrame",
            "Cria diretório de destino se necessário",
            "Exporta CSV com UTF-8-SIG e retorna metadados",
        ],
        "analisar_serie_temporal": [
            "Lê dados do cache (normal ou filtrado)",
            "Converte coluna de data para datetime",
            "Calcula métrica (count, sum ou mean)",
            "Retorna série para gráfico inline no chat",
        ],
        "analise_massiva_llm": [
            "Recupera dados do cache (filtrado ou completo)",
            "Valida se o volume está dentro do limite de 8.000 linhas",
            "Para cada linha, monta prompt com normativa + texto da reclamação",
            "Processa 5 requisições paralelas ao GPT-4 usando asyncio",
            "Coleta respostas JSON com as classificações de cada coluna de auditoria",
            "Preenche DataFrame com novas colunas criadas",
            "Salva resultado enriquecido em novo caminho no cache (ex: caminho_analise_massiva)",
            "Retorna JSON com caminho para exportação posterior via exportar_dataframe",
        ],
        "run_full_pipeline": [
            "Executa pipeline fim-a-fim de anomalias",
            "Realiza limpeza automática e criação de features",
            "Aplica múltiplos algoritmos de detecção (Ensemble)",
            "Interpreta resultados e gera relatório de insights",
        ],
        "detect_anomalies": [
            "Aplica algoritmos estatísticos e de ML (Isolation Forest, Z-Score, etc)",
            "Identifica comportamentos fora do padrão no dataset",
            "Permite segmentação por colunas categóricas",
        ],
    }
    return summaries.get(tool_name, ["Tool sem resumo detalhado cadastrado."])


@require_GET
def tool_detail_api(request):
    """Retorna explicação e código-fonte de uma tool específica."""
    tool_name = (request.GET.get("name") or "").strip()
    if not tool_name:
        return JsonResponse({"error": "Parâmetro 'name' é obrigatório."}, status=400)
    if tool_name not in TOOL_MAP:
        return JsonResponse({"error": f"Tool não encontrada: {tool_name}"}, status=404)

    fn = TOOL_MAP[tool_name]
    try:
        code = inspect.getsource(fn)
    except OSError:
        code = "# Código-fonte indisponível para esta tool."

    return JsonResponse(
        {
            "name": tool_name,
            "flow_summary": _tool_flow_summary(tool_name),
            "source_code": code,
        },
        json_dumps_params={"ensure_ascii": False},
    )


def _run_tool_call(tool_name: str, args: dict) -> tuple[dict, str, dict | None]:
    """Executa uma tool localmente e retorna args normalizados, resultado e metadado de download."""
    exported_file = None

    _UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    # ── Intercepta tools de exportação: redireciona saída para uploads/ ────
    if tool_name == "exportar_dataframe":
        original_name = Path(args.get("caminho_saida", "resultado.csv")).name
        safe_name = f"{uuid.uuid4().hex[:8]}_{original_name}"
        args["caminho_saida"] = str(_UPLOAD_DIR / safe_name)

    elif tool_name == "export_anomalies_csv":
        original_name = Path(args.get("output_path", "anomalias.csv")).name
        safe_name = f"{uuid.uuid4().hex[:8]}_{original_name}"
        args["output_path"] = str(_UPLOAD_DIR / safe_name)

    elif tool_name == "export_report":
        original_name = Path(args.get("output_path", "relatorio_anomalias.html")).name
        safe_name = f"{uuid.uuid4().hex[:8]}_{original_name}"
        args["output_path"] = str(_UPLOAD_DIR / safe_name)

    # filtros_json pode vir como lista (schema array) -> converte para string
    if tool_name == "filtrar_registros" and isinstance(args.get("filtros_json"), list):
        args["filtros_json"] = json.dumps(args["filtros_json"], ensure_ascii=False)

    resultado = TOOL_MAP[tool_name](**args)

    # ── Captura info do arquivo exportado (igual para todas as tools de export) ──
    if tool_name in ("exportar_dataframe", "export_anomalies_csv", "export_report"):
        try:
            r = json.loads(resultado)
            if r.get("sucesso") or r.get("status", "").startswith("✅"):
                exported_file = {
                    "name": original_name,
                    "url": f"/api/download/?file={safe_name}",
                }
                # Substitui resultado para não exibir caminho interno no chat
                resultado = json.dumps({"status": "✅ Arquivo gerado com sucesso.", "nome": original_name}, ensure_ascii=False)
        except Exception:
            pass

    return args, resultado, exported_file


def _extract_failed_tool_from_error(error_text: str) -> tuple[str, dict] | None:
    """Extrai (nome_da_tool, args) de mensagens tool_use_failed do Groq."""
    fn_prefix = r"(?<!\\w)(?:<|\\.)?function="

    # Formato 1: <function=nome={...}>
    m = re.search(rf"{fn_prefix}([a-zA-Z_][\\w]*)=(\{{.*\}})>", error_text)
    if m:
        tool_name = m.group(1)
        raw_args = m.group(2)
        try:
            return tool_name, json.loads(raw_args)
        except Exception:
            return None

    # Formato 2: <function=nome({...})>
    m = re.search(rf"{fn_prefix}([a-zA-Z_][\\w]*)\((\{{.*\}})\)>", error_text)
    if m:
        tool_name = m.group(1)
        raw_args = m.group(2)
        try:
            return tool_name, json.loads(raw_args)
        except Exception:
            return None

    # Formato 3: <function=nome>{...}
    m = re.search(rf"{fn_prefix}([a-zA-Z_][\\w]*)>\s*(\{{.*\}})", error_text)
    if m:
        tool_name = m.group(1)
        raw_args = m.group(2)
        try:
            return tool_name, json.loads(raw_args)
        except Exception:
            return None

    return None


def _extract_tool_calls_from_text(error_text: str) -> list[tuple[str, dict]]:
    """Extrai múltiplas chamadas no formato <function=nome>{...} ou .function=nome>{...}."""
    calls: list[tuple[str, dict]] = []

    matches = list(re.finditer(r"(?<!\w)(?:<|\.)?function=([a-zA-Z_][\w]*)>", error_text))
    if not matches:
        single = _extract_failed_tool_from_error(error_text)
        return [single] if single else []

    decoder = json.JSONDecoder()
    for idx, m in enumerate(matches):
        tool_name = m.group(1)
        seg_start = m.end()
        seg_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(error_text)
        segment = error_text[seg_start:seg_end]

        pos = 0
        while pos < len(segment):
            while pos < len(segment) and segment[pos].isspace():
                pos += 1
            if pos >= len(segment) or segment[pos] != "{":
                break
            try:
                obj, end_pos = decoder.raw_decode(segment, pos)
            except Exception:
                break

            if isinstance(obj, dict):
                calls.append((tool_name, obj))
            pos = end_pos

    if not calls:
        single = _extract_failed_tool_from_error(error_text)
        return [single] if single else []
    return calls


def _extract_history_style_tool_calls(text: str) -> list[tuple[str, dict]]:
    """Extrai chamadas no formato textual: [tool:nome] args={...}."""
    calls: list[tuple[str, dict]] = []
    decoder = json.JSONDecoder()

    for m in re.finditer(r"\[tool:([a-zA-Z_][\w]*)\]\s*args\s*=", text or ""):
        tool_name = m.group(1)
        pos = m.end()
        while pos < len(text) and text[pos].isspace():
            pos += 1
        if pos >= len(text) or text[pos] != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(text, pos)
        except Exception:
            continue
        if isinstance(obj, dict):
            calls.append((tool_name, obj))

    return calls


def _find_last_loaded_path(history: list[dict]) -> str | None:
    """Recupera o último caminho de arquivo usado nas tools da conversa."""
    for msg in reversed(history or []):
        if msg.get("role") != "assistant":
            continue
        for t in reversed(msg.get("toolsCalled", [])):
            args = t.get("args", {}) if isinstance(t, dict) else {}
            caminho = args.get("caminho")
            if isinstance(caminho, str) and caminho.strip():
                return caminho.strip()
    return None


# Mapa MCP id → agent_id correspondente em AGENTS
_MCP_TO_AGENT = {m["id"]: m["agent_id"] for m in MCP_SERVERS}
# IDs válidos para menção: MCPs + agentes diretos
_VALID_MENTION_IDS = set(_MCP_TO_AGENT.keys()) | {a["id"] for a in AGENTS}


def _detect_agent_mention(message: str) -> tuple[str | None, str]:
    """
    Detecta menção @mcp_xxx ou @agente_id na mensagem.
    Se for um MCP, resolve para o agent_id interno.
    Retorna (agent_id, mensagem_limpa) ou (None, mensagem_original).
    """
    # Caso 1: @id no INÍCIO da mensagem
    match = re.match(r'^@(\w+)\s+(.*)', message, re.DOTALL)
    if match and match.group(1) in _VALID_MENTION_IDS:
        raw_id = match.group(1)
        agent_id = _MCP_TO_AGENT.get(raw_id, raw_id)
        return agent_id, match.group(2).strip()

    # Caso 2: @id em qualquer posição
    match = re.search(r'@(\w+)', message)
    if match and match.group(1) in _VALID_MENTION_IDS:
        raw_id = match.group(1)
        agent_id = _MCP_TO_AGENT.get(raw_id, raw_id)
        clean = re.sub(r'@' + raw_id, '', message).strip()
        return agent_id, clean

    return None, message


# Prompt do roteador inteligente — conhece todo o sistema
_ROUTER_SYSTEM_PROMPT = """Você é o roteador inteligente de um sistema de auditoria corporativa com IA.
Sua única responsabilidade é ler a mensagem do usuário e decidir qual agente deve atendê-la.

## Princípio fundamental
Carregar um arquivo é apenas um meio — o que importa é o PROBLEMA que o usuário quer resolver.
Analise o contexto completo da mensagem, não apenas as palavras literais.

## Agentes disponíveis

### orquestrador_anomalias
Domínio: detecção de anomalias e comportamentos suspeitos em dados transacionais corporativos.
Exemplos de intenções (não se limite a estas palavras):
- Identificar gastos suspeitos, irregulares ou fora do padrão
- Analisar transações de cartão corporativo ou despesas
- Detectar outliers, comportamento atípico, fraudes internas
- Analisar padrões por pessoa, cargo, ramo, horário, dia da semana
- Qualquer investigação sobre dados financeiros transacionais de colaboradores
Sub-agentes: @agente_etl (carga), @agente_features (features), @agente_deteccao (algoritmos)

### orquestrador
Domínio: análise de reclamações de clientes e consultas à base interna FQ (AWS Athena).
Exemplos de intenções:
- Buscar, filtrar ou analisar reclamações de clientes
- Consultas SQL na base de reclamações FQ
- Análise temporal de volume de reclamações
- Qualquer investigação sobre insatisfação ou manifestações de clientes

### analise_massiva
Domínio: classificação qualitativa de textos com base em normativas e políticas.
Exemplos de intenções:
- Classificar conformidade, urgência, sentimento de reclamações
- Categorizar textos com base em uma política ou normativa

### gerador_sql
Domínio: criação de queries SQL para AWS Athena.
Exemplos de intenções:
- Escrever, criar ou gerar uma query SQL
- Montar uma consulta no Athena

## Regras de decisão

1. **Arquivo + contexto**: Carregar um arquivo é neutro. Avalie o restante da mensagem para entender o domínio.
   - Arquivo com contexto de transações/cartão/gastos/despesas/colaboradores → orquestrador_anomalias
   - Arquivo com contexto de reclamações/clientes/atendimento → orquestrador

2. **Ambiguidade**: Se o usuário carrega um arquivo sem dar contexto suficiente, analise o nome do arquivo.
   - Nomes como "transacional", "cartao", "despesa", "gasto", "if_" → orquestrador_anomalias
   - Nomes como "reclamacao", "ocorrencia", "fq_" → orquestrador

3. **Dúvida genuína**: Se mesmo assim não for possível determinar, prefira orquestrador_anomalias.

## Formato de resposta
Responda APENAS com JSON: {"agent": "<id>", "reason": "<motivo em 1 linha>"}
Valores válidos para agent: orquestrador, orquestrador_anomalias, agente_etl, agente_features, agente_deteccao, analise_massiva, gerador_sql
Nunca invente agentes que não existem na lista acima."""


def _llm_route_agent(message: str) -> str | None:
    """
    Usa o LLM para determinar qual agente deve atender a mensagem.
    Retorna agent_id ou None (usa orquestrador padrão).
    """
    try:
        import uuid as _uuid
        router_client = IaraGenAI(
            client_id=os.environ.get("IARA_CLIENT_ID"),
            client_secret=os.environ.get("IARA_CLIENT_SECRET"),
            environment=os.environ.get("IARA_ENVIRONMENT", "homol"),
            provider=os.environ.get("IARA_PROVIDER", "azure_openai"),
            correlation_id=str(_uuid.uuid4()),
        )
        # Usa modelo leve para roteamento (rápido e barato)
        router_model = os.environ.get("IARA_MODEL_ROUTER", os.environ.get("IARA_MODEL_MASSIVA", DEFAULT_MODEL))
        response = router_client.chat.completions.create(
            model=router_model,
            messages=[
                {"role": "system", "content": _ROUTER_SYSTEM_PROMPT},
                {"role": "user", "content": message},
            ],
            temperature=0,
            response_format={"type": "json_object"},
        )
        result = json.loads(response.choices[0].message.content)
        agent_id = result.get("agent", "").strip()
        reason = result.get("reason", "")
        valid_ids = {a["id"] for a in AGENTS} | {"orquestrador"}
        if agent_id in valid_ids and agent_id != "orquestrador":
            import logging
            logging.getLogger(__name__).info(f"[Router] → {agent_id} | {reason}")
            return agent_id
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"[Router] Falha no roteamento LLM: {e}")
    return None


def _get_agent_config(agent_id: str | None, message: str = "") -> dict:
    """
    Retorna configuração do agente.
    Se agent_id for None → modo conversacional (sem tools).
    Se agent_id informado → ativa 100% o MCP/agente correspondente.
    """
    # Sem menção = chat conversacional puro (sem tools, sem roteamento)
    if not agent_id:
        return {
            "model": DEFAULT_MODEL,
            "tools": [],
            "system_prompt": CONVERSATIONAL_PROMPT,
            "name": "Chat Conversacional",
            "auto_routed": False,
        }

    for agent in AGENTS:
        if agent["id"] == agent_id:
            # Usa pool de tools específico do agente se existir, senão usa TOOLS global
            tools_pool = _AGENT_TOOLS_MAP.get(agent["id"], TOOLS)
            agent_tools = [
                tool for tool in tools_pool
                if tool["function"]["name"] in agent["tools"]
            ]
            return {
                "model": agent["model"],
                "tools": agent_tools if agent_tools else tools_pool,
                "system_prompt": agent.get("system_prompt", SYSTEM_PROMPT),
                "name": agent["name"],
                "auto_routed": True,
            }

    return {
        "model": DEFAULT_MODEL,
        "tools": TOOLS,
        "system_prompt": SYSTEM_PROMPT,
        "name": "Orquestrador Principal",
        "auto_routed": False,
    }



def _sanitize_tool_args(tool_name: str, args: dict, fallback_caminho: str | None) -> dict:
    """Normaliza args vindos de function-like text para melhorar robustez."""
    if not isinstance(args, dict):
        return args

    # Remove espaços acidentais nas chaves e normaliza strings
    clean: dict = {}
    for k, v in args.items():
        key = str(k).strip()
        clean[key] = v

    # Alguns modelos inventam payload dataframe; ignoramos e usamos o caminho carregado
    clean.pop("dataframe", None)

    if tool_name == "analisar_serie_temporal":
        caminho = clean.get("caminho")
        if not caminho and fallback_caminho:
            clean["caminho"] = fallback_caminho

        usar_filtrado = clean.get("usar_cache_filtrado")
        if isinstance(usar_filtrado, str):
            val = usar_filtrado.strip().lower()
            if val in {"true", "1", "sim", "yes"}:
                clean["usar_cache_filtrado"] = True
            elif val in {"false", "0", "nao", "não", "no"}:
                clean["usar_cache_filtrado"] = False

        metrica = clean.get("metrica")
        if isinstance(metrica, str):
            clean["metrica"] = metrica.strip().lower()

    return clean


def chat_view(request):
    """Renderiza a interface principal do chat."""
    return render(request, "chat/index.html")


# Extensões permitidas para upload de planilhas
_ALLOWED_EXTENSIONS = {".csv", ".xlsx", ".xls"}
_UPLOAD_DIR = settings.BASE_DIR / "uploads"


@csrf_exempt
@require_POST
def upload_file(request):
    """Recebe um arquivo Excel/CSV, salva em /uploads/ e retorna o caminho."""
    file = request.FILES.get("file")
    if not file:
        return JsonResponse({"error": "Nenhum arquivo enviado."}, status=400)

    ext = Path(file.name).suffix.lower()
    if ext not in _ALLOWED_EXTENSIONS:
        return JsonResponse(
            {"error": f"Tipo não permitido. Use: {', '.join(_ALLOWED_EXTENSIONS)}"},
            status=400,
        )

    # Limita 900 MB
    if file.size > 900 * 1024 * 1024:
        return JsonResponse({"error": "Arquivo muito grande. Limite: 900 MB."}, status=400)

    _UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    # Nome único para evitar colisões
    safe_name = f"{uuid.uuid4().hex[:8]}_{Path(file.name).stem}{ext}"
    dest = _UPLOAD_DIR / safe_name

    with open(dest, "wb") as f:
        for chunk in file.chunks():
            f.write(chunk)

    return JsonResponse({"path": str(dest), "name": file.name})


@csrf_exempt
@require_POST
def chat_api(request):
    """Endpoint da API que processa a mensagem e retorna a resposta do assistente."""
    try:
        data = json.loads(request.body)
        user_message = data.get("message", "").strip()
        history = data.get("history", [])
        requested_model = (data.get("model") or "").strip()
        # last_loaded_path: primeiro tenta do histórico filtrado enviado; depois usa
        # o campo explícito (que pode vir de outro MCP, pois o cache é compartilhado)
        last_loaded_path = _find_last_loaded_path(history) or data.get("last_file_path") or None

        if not user_message:
            return JsonResponse({"error": "Mensagem vazia."}, status=400)
        
        # Detecta menção de MCP (@mcp_xxx) ou agente (@agente_id)
        mentioned_agent, clean_message = _detect_agent_mention(user_message)
        # Sem menção = modo conversacional (sem tools); com menção = MCP 100%
        agent_config = _get_agent_config(mentioned_agent)
        
        # Se usuário solicitou modelo específico via UI, usa ele; senão usa do agente
        if requested_model and requested_model in MODEL_OPTIONS:
            selected_model = requested_model
        else:
            selected_model = agent_config["model"]
        
        # Tools disponíveis para este agente
        available_tools = agent_config["tools"]
        agent_system_prompt = agent_config["system_prompt"]
        agent_name = agent_config["name"]
        auto_routed = agent_config.get("auto_routed", False)
        
        # Se mencionou um agente, usa a mensagem limpa (sem @agente)
        final_message = clean_message if mentioned_agent else user_message

        messages = [{"role": "system", "content": agent_system_prompt}]
        
        # Informa ao modelo qual agente está ativo (menção manual ou roteamento automático)
        if mentioned_agent:
            messages.append({"role": "system", "content": f"Você está atuando como: {agent_name}"})
        elif auto_routed:
            messages.append({"role": "system", "content": f"[Roteamento automático] Você está atuando como: {agent_name}"})
        
        for msg in history:
            role = msg.get("role")
            if role == "user":
                messages.append({"role": "user", "content": msg["content"]})
            elif role == "assistant":
                tools_called_hist = msg.get("toolsCalled", [])
                if tools_called_hist:
                    # Inclui resumo das tools no conteúdo para o modelo manter contexto
                    tool_summary = "\n".join(
                        f"[tool:{t['tool']}] args={json.dumps(t['args'], ensure_ascii=False)} "
                        f"→ {str(t['result'])[:300]}"
                        for t in tools_called_hist
                    )
                    content = f"[Ferramentas chamadas nesta etapa]\n{tool_summary}\n\n{msg['content']}"
                    messages.append({"role": "assistant", "content": content})
                else:
                    messages.append({"role": "assistant", "content": msg["content"]})
        messages.append({"role": "user", "content": final_message})

        tools_called = []
        exported_file = None  # preenchido se exportar_dataframe for chamado
        msg = None

        # ── Modo conversacional (sem tools) ──────────────────────
        if not available_tools:
            response = _client.chat.completions.create(
                model=selected_model,
                messages=messages,
            )
            answer = response.choices[0].message.content
            return JsonResponse({
                "answer": answer,
                "tools_called": [],
                "exported_file": None,
            })

        # ── Modo MCP (com tools) ─────────────────────────────────
        # Loop de raciocínio: continua executando tools até o modelo responder com texto
        MAX_ITERATIONS = 10
        for _iteration in range(MAX_ITERATIONS):
            try:
                response = _client.chat.completions.create(
                    model=selected_model,
                    messages=messages,
                    tools=available_tools,
                    tool_choice="auto",
                )
                msg = response.choices[0].message
            except Exception as exc:
                # Fallback para casos de tool_use_failed com failed_generation
                parsed_calls = _extract_tool_calls_from_text(str(exc))
                if not parsed_calls:
                    raise

                executed = []
                for nome, args in parsed_calls:
                    if nome not in TOOL_MAP:
                        continue
                    args = _sanitize_tool_args(nome, args, last_loaded_path)
                    args, resultado, exported_file_fallback = _run_tool_call(nome, args)
                    if exported_file_fallback:
                        exported_file = exported_file_fallback
                    tools_called.append({"tool": nome, "args": args, "result": resultado})
                    executed.append((nome, resultado))

                if not executed:
                    raise

                nome = executed[0][0]
                resultado = "\n".join(f"[{n}] {r}" for n, r in executed)

                messages_fallback = messages + [
                    {"role": "assistant", "content": f"Ferramenta executada automaticamente: {nome}."},
                    {"role": "assistant", "content": f"Resultado da ferramenta: {resultado}"},
                ]
                response_final = _client.chat.completions.create(
                    model=selected_model,
                    messages=messages_fallback,
                )
                answer = response_final.choices[0].message.content

                return JsonResponse({
                    "answer": answer,
                    "tools_called": tools_called,
                    "exported_file": exported_file,
                })

            # Se o modelo não pediu nenhuma tool, ele terminou o raciocínio
            if not msg.tool_calls:
                # Verifica fallbacks de tool chamadas em texto
                parsed_inline_calls = _extract_tool_calls_from_text(msg.content or "")
                if not parsed_inline_calls:
                    parsed_inline_calls = _extract_history_style_tool_calls(msg.content or "")

                if parsed_inline_calls:
                    # Modelo escreveu a chamada de tool em texto — executa e continua o loop
                    for nome, args in parsed_inline_calls:
                        if nome not in TOOL_MAP:
                            continue
                        args = _sanitize_tool_args(nome, args, last_loaded_path)
                        args, resultado, exported_file_delta = _run_tool_call(nome, args)
                        if exported_file_delta:
                            exported_file = exported_file_delta
                        tools_called.append({"tool": nome, "args": args, "result": resultado})
                        messages.append({
                            "role": "assistant",
                            "content": f"Ferramenta executada: {nome}. Resultado: {str(resultado)[:300]}",
                        })
                    continue  # próxima iteração do loop

                # Nenhuma tool mais — esta é a resposta final
                answer = msg.content
                break

            # Modelo pediu tools via function calling — executa todas e continua o loop
            msg_dict = {
                "role": "assistant",
                "content": msg.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in msg.tool_calls
                ],
            }
            messages.append(msg_dict)

            for tc in msg.tool_calls:
                nome = tc.function.name
                args = json.loads(tc.function.arguments)
                args = _sanitize_tool_args(nome, args, last_loaded_path)

                args, resultado, exported_file_delta = _run_tool_call(nome, args)
                if exported_file_delta:
                    exported_file = exported_file_delta
                tools_called.append({"tool": nome, "args": args, "result": resultado})

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": str(resultado),
                })
        else:
            # Atingiu MAX_ITERATIONS sem resposta final
            answer = "Limite de iterações atingido sem uma resposta conclusiva."

        return JsonResponse({
            "answer": answer,
            "tools_called": tools_called,
            "exported_file": exported_file,
        })

    except Exception as exc:
        import traceback
        error_details = traceback.format_exc()
        print(f"[chat_api ERROR] {error_details}")  # Log to Django console
        return JsonResponse({"error": str(exc), "traceback": error_details}, status=500)


@require_GET
def progresso_api(request):
    """Retorna o progresso atual de operações longas (ex: lematização)."""
    return JsonResponse(server_reclamacao.get_progresso())


def settings_view(request):
    """Renderiza a tela de configurações."""
    return render(request, "chat/settings.html")


@require_GET
def download_file(request):
    """Serve arquivos exportados de /uploads/ para download seguro."""
    filename = request.GET.get("file") or ""
    if not filename or "/" in filename or "\\" in filename or ".." in filename:
        raise Http404

    file_path = _UPLOAD_DIR / filename
    # Só serve arquivos dentro de uploads/ que existam
    if not file_path.exists() or not file_path.is_file():
        raise Http404
    try:
        file_path.resolve().relative_to(_UPLOAD_DIR.resolve())
    except ValueError:
        raise Http404

    content_type, _ = mimetypes.guess_type(str(file_path))
    content_type = content_type or "application/octet-stream"
    response = FileResponse(open(file_path, "rb"), content_type=content_type)
    # Garante que filename nunca é None para evitar erro de header
    safe_filename = str(filename) if filename else "download"
    response["Content-Disposition"] = f'attachment; filename="{safe_filename}"'
    return response



def settings_api(request):
    """Retorna JSON com tools, agentes e configurações atuais."""
    tools_data = [
        {
            "name": t["function"]["name"],
            "description": t["function"]["description"],
            "parameters": t["function"]["parameters"].get("properties", {}),
            "required": t["function"]["parameters"].get("required", []),
        }
        for t in TOOLS
    ]
    agents_data = [
        {
            "id": a["id"],
            "name": a["name"],
            "description": a["description"],
            "model": a["model"],
            "provider": a.get("provider", "azure_openai"),
            "tools": a.get("tools", []),
            "system_prompt": a.get("system_prompt", ""),
            "status": a.get("status", "ativo"),
        }
        for a in AGENTS
    ]
    mcps_data = [
        {
            "id": m["id"],
            "name": m["name"],
            "icon": m.get("icon", "🔧"),
            "description": m["description"],
            "model": m["model"],
            "cache_compartilhado": m.get("cache_compartilhado", False),
        }
        for m in MCP_SERVERS
    ]
    return JsonResponse({
        "tools": tools_data,
        "agents": agents_data,
        "mcps": mcps_data,
        "default_model": DEFAULT_MODEL,
        "model_options": MODEL_OPTIONS,
    })


@csrf_exempt
def save_agent_models(request):
    """Salva as configurações de modelo por agente."""
    if request.method != "POST":
        return JsonResponse({"error": "Método não permitido"}, status=405)
    
    try:
        data = json.loads(request.body)
        agent_models = data.get("agent_models", {})
        
        # Atualiza as variáveis de ambiente em memória
        for agent_id, model in agent_models.items():
            if agent_id == "orquestrador":
                os.environ["IARA_MODEL_ORQUESTRADOR"] = model
            elif agent_id == "analise_massiva":
                os.environ["IARA_MODEL_MASSIVA"] = model
            elif agent_id == "gerador_sql":
                os.environ["IARA_MODEL_SQL"] = model
        
        # Atualiza os agentes em agent_config
        from . import agent_config
        import importlib
        importlib.reload(agent_config)
        
        return JsonResponse({
            "success": True,
            "message": "Configurações salvas com sucesso",
            "agent_models": agent_models
        })
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)
