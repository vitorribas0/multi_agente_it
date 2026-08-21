import os
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────────────
# Configuração central dos agentes e tools do sistema MCP Reclamações.
# Edite apenas este arquivo para mudar modelos, system prompts ou tools.
# ─────────────────────────────────────────────────────────────────────────────

# Modelos por componente (lê do .env, com fallback)
DEFAULT_MODEL_ORQUESTRADOR = os.environ.get("IARA_MODEL_ORQUESTRADOR", "gpt-5.2")
DEFAULT_MODEL_MASSIVA = os.environ.get("IARA_MODEL_MASSIVA", "gpt-5-mini")
DEFAULT_MODEL_SQL = os.environ.get("IARA_MODEL_SQL", "gpt-5.1-codex-max")
DEFAULT_MODEL = os.environ.get("IARA_MODEL", DEFAULT_MODEL_ORQUESTRADOR)
DEFAULT_PROVIDER = "azure_openai"

# Modelos disponíveis para seleção na UI de configurações
MODEL_OPTIONS = [
    "gpt-5.2",
    "gpt-5.1",
    "gpt-5",
    "gpt-5-mini",
    "gpt-5-nano",
    "gpt-5.1-codex-max",
    "gpt-4o",
    "gpt-4o-mini",
]

# System prompt do orquestrador geral — mora em src/prompts/ (fora de qualquer MCP)
_prompt_path = Path(__file__).resolve().parent.parent.parent / "src" / "prompts" / "orquestrador_geral.md"

if _prompt_path.exists():
    with open(_prompt_path, "r", encoding="utf-8") as f:
        SYSTEM_PROMPT = f.read()
else:
    # Fallback: legado no mcp_reclamacao
    _prompt_path_legacy = Path(__file__).resolve().parent.parent.parent / "src" / "mcp_reclamacao" / "prompts" / "system_prompt.md"
    if _prompt_path_legacy.exists():
        with open(_prompt_path_legacy, "r", encoding="utf-8") as f:
            SYSTEM_PROMPT = f.read()
    else:
        SYSTEM_PROMPT = "Você é o orquestrador geral de auditoria corporativa."

# Carrega prompts específicos de cada agente
_prompts_dir = Path(__file__).resolve().parent.parent.parent / "src" / "mcp_reclamacao" / "prompts"
_prompts_anomalias_dir = Path(__file__).resolve().parent.parent.parent / "src" / "mcp_anomalias" / "prompts"

def _load_prompt(filename: str, fallback: str, is_anomalias: bool = False) -> str:
    """Carrega prompt de arquivo ou retorna fallback."""
    dir_path = _prompts_anomalias_dir if is_anomalias else _prompts_dir
    path = dir_path / filename
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    return fallback

PROMPT_ANALISE_MASSIVA = _load_prompt(
    "analise_massiva_agent.md",
    "Você é um classificador preciso e rápido."
)

PROMPT_GERADOR_SQL = _load_prompt(
    "gerador_sql_athena.md",
    "Você é um expert em SQL e AWS Athena."
)

PROMPT_ORQUESTRADOR_ANOMALIAS = _load_prompt(
    "orquestrador_anomalias.md",
    "Você é o orquestrador do pipeline de detecção de anomalias.",
    is_anomalias=True
)

PROMPT_AGENTE_ETL = _load_prompt(
    "agente_etl_anomalias.md",
    "Você é especialista em ETL e preparação de dados.",
    is_anomalias=True
)

PROMPT_AGENTE_FEATURES = _load_prompt(
    "agente_features_anomalias.md",
    "Você é especialista em feature engineering para anomalias.",
    is_anomalias=True
)

PROMPT_AGENTE_DETECCAO = _load_prompt(
    "agente_deteccao_anomalias.md",
    "Você é especialista em detecção e interpretação de anomalias.",
    is_anomalias=True
)

# Definição das tools no formato OpenAI/Groq function calling

# Definição das tools no formato OpenAI/Groq function calling
# Organizadas por categoria: Carregamento → Filtragem → NLP → Análise → Exportação
TOOLS = [
    # ═══════════════════════════════════════════════════════════════════════════
    # CARREGAMENTO DE DADOS
    # ═══════════════════════════════════════════════════════════════════════════
    {
        "type": "function",
        "function": {
            "name": "query_fq_database",
            "description": (
                "Consulta a base FQ (AWS) de reclamações usando SQL. "
                "Retorna resultados em JSON e armazena em cache para exportação. "
                "Base: database_rt2.RT2_AI6_OCORRENCIA_FQ_001. "
                "Campos principais: idassuntoocorrido, documento, anomesdia, nomeassunto, nometipoassunto, descricao, relato, tipopessoa."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query_sql": {"type": "string", "description": "Query SQL completa para executar no Athena"},
                    "limit": {"type": "integer", "description": "Opcional: Limite de linhas. Use -1 para extração total (sem limites)."},
                },
                "required": ["query_sql"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "carregar_arquivo",
            "description": (
                "Carrega um arquivo CSV ou Excel. "
                "Armazena todos os registros internamente para processamento em lote. "
                "Retorna ao modelo apenas o schema (colunas) e 3 linhas de preview. "
                "Suporta .csv, .xlsx e .xls."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "caminho": {"type": "string", "description": "Caminho absoluto do arquivo CSV ou Excel"},
                },
                "required": ["caminho"],
            },
        },
    },
    # ═══════════════════════════════════════════════════════════════════════════
    # FILTRAGEM E MANIPULAÇÃO
    # ═══════════════════════════════════════════════════════════════════════════
    {
        "type": "function",
        "function": {
            "name": "filtrar_registros",
            "description": (
                "Filtra o dataframe já carregado em memória com base em condições informadas. "
                "Recebe uma lista JSON de filtros, cada um com 'coluna', 'operador' e 'valor'. "
                "Operadores suportados: ==, !=, >, <, >=, <=, contains, startswith, endswith, isnull, notnull. "
                "O resultado filtrado fica em cache e pode ser exportado com exportar_dataframe."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "caminho": {"type": "string", "description": "Caminho do arquivo já carregado"},
                    "filtros_json": {
                        "type": "array",
                        "description": "Lista de filtros a aplicar",
                        "items": {
                            "type": "object",
                            "properties": {
                                "coluna":   {"type": "string", "description": "Nome da coluna"},
                                "operador": {"type": "string", "description": "Operador: ==, !=, >, <, >=, <=, contains, startswith, endswith, isnull, notnull"},
                                "valor":    {"description": "Valor para comparar (omitir em isnull/notnull)"}
                            },
                            "required": ["coluna", "operador"]
                        }
                    },
                },
                "required": ["caminho", "filtros_json"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "filtrar_por_palavras",
            "description": (
                "Filtra registros que mencionam uma ou mais palavras-chave em uma coluna. "
                "Para melhores resultados, normalize a coluna primeiro com normalizar_nlp e use a coluna '_limpo'. "
                "O resultado fica em cache e pode ser exportado com exportar_dataframe."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "caminho": {"type": "string", "description": "Caminho do arquivo já carregado"},
                    "coluna": {"type": "string", "description": "Nome da coluna onde buscar as palavras"},
                    "palavras": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Lista de palavras-chave a buscar (case-insensitive)",
                    },
                },
                "required": ["caminho", "coluna", "palavras"],
            },
        },
    },
    # ═══════════════════════════════════════════════════════════════════════════
    # PROCESSAMENTO NLP
    # ═══════════════════════════════════════════════════════════════════════════
    {
        "type": "function",
        "function": {
            "name": "normalizar_nlp",
            "description": (
                "Normaliza texto de uma coluna do dataframe em cache: converte para minúsculas, "
                "remove acentos e pontuação. Cria uma nova coluna com sufixo '_limpo'. "
                "Use antes de filtrar por palavras para melhorar a busca."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "caminho": {"type": "string", "description": "Caminho do arquivo já carregado"},
                    "coluna": {"type": "string", "description": "Nome da coluna de texto a normalizar"},
                },
                "required": ["caminho", "coluna"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lematizar_nlp",
            "description": (
                "Lematiza uma coluna do dataframe (ex: 'reclamacoes' -> 'reclamacao'). "
                "Utiliza o modelo spaCy pt_core_news_sm. "
                "Use após normalizar_nlp para melhorar a busca semântica."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "caminho": {"type": "string", "description": "Caminho do arquivo já carregado"},
                    "coluna": {"type": "string", "description": "Nome da coluna de texto para lematização"},
                },
                "required": ["caminho", "coluna"],
            },
        },
    },
    # ═══════════════════════════════════════════════════════════════════════════
    # ANÁLISE E AUDITORIA
    # ═══════════════════════════════════════════════════════════════════════════
    {
        "type": "function",
        "function": {
            "name": "analisar_serie_temporal",
            "description": (
                "Realiza análise de série temporal em uma coluna de data. "
                "Permite agrupar por Dia (D), Mês (MS) ou Ano (YS). "
                "Calcula métricas como contagem (count), soma (sum) ou média (mean). "
                "Retorna os pontos da série para exibição de gráfico inline."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "caminho": {"type": "string", "description": "Caminho do arquivo já carregado"},
                    "coluna_data": {"type": "string", "description": "Nome da coluna de data"},
                    "frequencia": {"type": "string", "description": "Frequência: D (dia), MS (mês), YS (ano)", "default": "MS"},
                    "metrica": {"type": "string", "description": "Métrica: count, sum, mean", "default": "count"},
                    "coluna_valor": {"type": "string", "description": "Coluna numérica para sum/mean (opcional)"},
                    "usar_cache_filtrado": {"type": "boolean", "description": "Se true, usa cache filtrado", "default": False},
                },
                "required": ["caminho", "coluna_data"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analise_massiva_llm",
            "description": (
                "Realiza análise massiva de reclamações usando IA para classificar linhas com base em critérios. "
                "LIMITAÇÃO: O volume máximo é de 8.000 linhas. Caso exceda, a IA deve alertar o usuário. "
                "Processamento assíncrono com 5 registros em paralelo. "
                "É necessário passar o conteúdo da normativa/política, as colunas que deseja criar e a coluna com o texto da reclamação."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "caminho": {"type": "string", "description": "Caminho do arquivo de origem (já carregado em cache)"},
                    "coluna_texto": {"type": "string", "description": "Nome da coluna que contém o texto da reclamação"},
                    "colunas_auditoria": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Lista com os nomes das colunas que serão criadas (ex: ['Conformidade', 'Urgência'])"
                    },
                    "contexto_normativa": {"type": "string", "description": "Texto completo da política ou normativa que será usada como base para a classificação"},
                },
                "required": ["caminho", "coluna_texto", "colunas_auditoria", "contexto_normativa"],
            },
        },
    },
    # ═══════════════════════════════════════════════════════════════════════════
    # EXPORTAÇÃO
    # ═══════════════════════════════════════════════════════════════════════════
    {
        "type": "function",
        "function": {
            "name": "exportar_dataframe",
            "description": (
                "Exporta o dataframe para um arquivo CSV. "
                "Por padrão exporta o dataframe filtrado (se houver filtro aplicado). "
                "Passe usar_filtrado=false para exportar o dataframe completo original."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "caminho": {"type": "string", "description": "Caminho do arquivo de origem (já carregado)"},
                    "caminho_saida": {"type": "string", "description": "Caminho do arquivo CSV de saída"},
                    "usar_filtrado": {"type": "boolean", "description": "Se true (padrão), exporta o filtrado; se false, exporta o original completo"},
                },
                "required": ["caminho", "caminho_saida"],
            },
        },
    },
]

# Metadados dos agentes (usado na tela de configurações)

# Tools do MCP Anomalias no formato OpenAI function calling
TOOLS_ANOMALIAS = [
    {
        "type": "function",
        "function": {
            "name": "importar_dados_reclamacao",
            "description": (
                "Importa automaticamente os dados já extraídos pelo agente de reclamações (@mcp_reclamacao) "
                "para o ambiente de análise de anomalias, sem precisar de arquivo ou JSON. "
                "Use SEMPRE que o usuário quiser analisar dados que foram buscados/filtrados na conversa anterior com o mcp_reclamacao."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "dataset_id": {
                        "type": "string",
                        "description": "Nome para identificar o dataset (padrão 'reclamacoes')"
                    },
                    "usar_filtrado": {
                        "type": "boolean",
                        "description": "Se true (padrão), importa o resultado filtrado; se false, importa o dataset completo"
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "load_data",
            "description": "Carrega um arquivo CSV ou Excel para análise de anomalias. Salva em cache com um dataset_id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Caminho absoluto para o arquivo CSV ou Excel"},
                    "sep": {"type": "string", "description": "Separador do CSV (padrão ','; use ';' para arquivos brasileiros)"},
                    "encoding": {"type": "string", "description": "Encoding do arquivo (padrão 'utf-8')"},
                    "dataset_id": {"type": "string", "description": "Nome para identificar o dataset nas outras ferramentas (padrão 'main')"},
                },
                "required": ["file_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_datasets",
            "description": "Lista todos os datasets carregados em memória.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "preview_data",
            "description": "Exibe as primeiras linhas do dataset carregado.",
            "parameters": {
                "type": "object",
                "properties": {
                    "dataset_id": {"type": "string", "description": "ID do dataset (padrão 'main')"},
                    "n_rows": {"type": "integer", "description": "Número de linhas para exibir (padrão 10)"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "assess_data_quality",
            "description": "Avalia a qualidade do dataset: valores nulos, duplicatas, tipos de coluna e distribuições.",
            "parameters": {
                "type": "object",
                "properties": {
                    "dataset_id": {"type": "string", "description": "ID do dataset (padrão 'main')"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "auto_clean_data",
            "description": "Limpa automaticamente o dataset: trata nulos, remove duplicatas, normaliza tipos.",
            "parameters": {
                "type": "object",
                "properties": {
                    "dataset_id": {"type": "string", "description": "ID do dataset"},
                    "strategy": {"type": "string", "description": "Estratégia: 'conservative' (padrão), 'moderate' ou 'aggressive'"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "suggest_features",
            "description": "Sugere as melhores features para detecção de anomalias, rankeadas por relevância.",
            "parameters": {
                "type": "object",
                "properties": {
                    "dataset_id": {"type": "string", "description": "ID do dataset (padrão 'main')"},
                    "top_n": {"type": "integer", "description": "Número de sugestões a retornar (padrão 8)"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "apply_high_confidence_features",
            "description": "Aplica automaticamente as features de alta confiança, criando novas colunas no dataset.",
            "parameters": {
                "type": "object",
                "properties": {
                    "dataset_id": {"type": "string", "description": "ID do dataset"},
                    "top_n": {"type": "integer", "description": "Número de features a aplicar (padrão 5)"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_for_detection",
            "description": "Analisa as colunas numéricas e recomenda o melhor método de detecção de anomalias.",
            "parameters": {
                "type": "object",
                "properties": {
                    "dataset_id": {"type": "string", "description": "ID do dataset (padrão 'main')"},
                    "numeric_cols": {"type": "string", "description": "Colunas numéricas separadas por vírgula (auto se vazio)"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "detect_anomalies",
            "description": (
                "Detecta anomalias no dataset. Métodos disponíveis: 'auto', 'box_plot', 'z_score', "
                "'modified_z_score', 'isolation_forest', 'local_outlier_factor', 'ensemble'. "
                "Suporta segmentação por coluna (ex: por cargo, ramo, empresa)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "dataset_id": {"type": "string", "description": "ID do dataset"},
                    "numeric_cols": {"type": "string", "description": "Colunas numéricas separadas por vírgula (auto se vazio)"},
                    "method": {"type": "string", "description": "Método: 'auto', 'box_plot', 'z_score', 'modified_z_score', 'isolation_forest', 'local_outlier_factor', 'ensemble'"},
                    "threshold": {"type": "number", "description": "Threshold customizado (usa padrão do método se vazio)"},
                    "segment_by": {"type": "string", "description": "Coluna para segmentar a análise (ex: 'cargo', 'ramo_1')"},
                    "result_id": {"type": "string", "description": "ID para salvar o resultado (padrão 'latest')"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_full_pipeline",
            "description": (
                "Executa o pipeline COMPLETO automaticamente: preparação de dados + feature engineering "
                "+ detecção de anomalias + interpretação dos resultados. "
                "Use quando o usuário quiser uma análise completa sem etapas manuais."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "dataset_id": {"type": "string", "description": "ID do dataset"},
                    "numeric_cols": {"type": "string", "description": "Colunas numéricas separadas por vírgula (auto se vazio)"},
                    "user_problem": {"type": "string", "description": "Descrição do problema que o usuário quer investigar"},
                    "mode": {"type": "string", "description": "Modo: 'quick', 'standard' (padrão) ou 'comprehensive'"},
                    "result_id": {"type": "string", "description": "ID para salvar os resultados (padrão 'latest')"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_insights",
            "description": "Interpreta os resultados da detecção: top anomalias, padrões identificados e recomendações.",
            "parameters": {
                "type": "object",
                "properties": {
                    "result_id": {"type": "string", "description": "ID dos resultados de detecção (padrão 'latest')"},
                    "top_n": {"type": "integer", "description": "Número de anomalias a detalhar (padrão 10)"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "explain_anomaly",
            "description": "Explica detalhadamente por que uma linha específica foi classificada como anômala.",
            "parameters": {
                "type": "object",
                "properties": {
                    "row_index": {"type": "integer", "description": "Índice da linha no dataset"},
                    "dataset_id": {"type": "string", "description": "ID do dataset (padrão 'main')"},
                    "numeric_cols": {"type": "string", "description": "Colunas numéricas separadas por vírgula"},
                },
                "required": ["row_index"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "export_anomalies_csv",
            "description": "Exporta apenas as linhas anômalas detectadas em formato CSV para download.",
            "parameters": {
                "type": "object",
                "properties": {
                    "result_id": {"type": "string", "description": "ID dos resultados (padrão 'latest')"},
                    "dataset_id": {"type": "string", "description": "ID do dataset (padrão 'main')"},
                    "output_path": {"type": "string", "description": "Caminho de saída do CSV"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "export_report",
            "description": "Gera relatório HTML completo com visualizações e análises dos resultados.",
            "parameters": {
                "type": "object",
                "properties": {
                    "result_id": {"type": "string", "description": "ID dos resultados (padrão 'latest')"},
                    "output_path": {"type": "string", "description": "Caminho de saída do HTML"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "execute_python",
            "description": (
                "Executa código Python arbitrário gerado pelo cientista de dados. "
                "Use quando nenhuma outra tool resolver o problema. "
                "O DataFrame principal está disponível como `df`. "
                "Use print() para retornar resultados ao usuário. "
                "Para salvar resultado, atribua a `result_df`."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Código Python a executar"},
                    "dataset_id": {"type": "string", "description": "Dataset disponível como `df` (padrão 'main')"},
                    "save_result_as": {"type": "string", "description": "ID para salvar `result_df` criada pelo código"},
                },
                "required": ["code"],
            },
        },
    },
]

AGENTS = [
    {
        "id": "orquestrador",
        "name": "🧠 Orquestrador Principal",
        "description": (
            "Agente de raciocínio complexo. Responsável por interpretar requisições, "
            "planejar estratégias e orquestrar o uso de ferramentas."
        ),
        "model": DEFAULT_MODEL_ORQUESTRADOR,
        "provider": DEFAULT_PROVIDER,
        "tools": [t["function"]["name"] for t in TOOLS],
        "system_prompt": SYSTEM_PROMPT,
        "status": "ativo",
    },
    {
        "id": "analise_massiva",
        "name": "🤖 Motor de Análise Massiva",
        "description": (
            "Especialista em classificação em larga escala. Processa até 8.000 linhas "
            "com análise de IA (modelo econômico otimizado para volume)."
        ),
        "model": DEFAULT_MODEL_MASSIVA,
        "provider": DEFAULT_PROVIDER,
        "tools": ["analise_massiva_llm"],
        "system_prompt": PROMPT_ANALISE_MASSIVA,
        "status": "ativo",
    },
    {
        "id": "gerador_sql",
        "name": "💾 Gerador de SQL",
        "description": (
            "Especialista em gerar queries SQL otimizadas para o Athena. "
            "Usa modelo especializado em código (Codex)."
        ),
        "model": DEFAULT_MODEL_SQL,
        "provider": DEFAULT_PROVIDER,
        "tools": ["query_fq_database"],
        "system_prompt": PROMPT_GERADOR_SQL,
        "status": "ativo",
    },
    # ──────────────────────────────────────────
    # MCP ANOMALIAS
    # ──────────────────────────────────────────
    {
        "id": "orquestrador_anomalias",
        "name": "🔍 Orquestrador de Anomalias",
        "description": (
            "Coordena o pipeline completo de detecção de anomalias. "
            "Recebe o pedido do usuário, planeja a sequência ETL → Features → Detecção e orienta cada etapa."
        ),
        "model": DEFAULT_MODEL_ORQUESTRADOR,
        "provider": DEFAULT_PROVIDER,
        "tools": [
            "importar_dados_reclamacao", "load_data", "list_datasets", "preview_data", "assess_data_quality",
            "auto_clean_data", "suggest_features", "apply_high_confidence_features",
            "analyze_for_detection", "detect_anomalies", "run_full_pipeline",
            "get_insights", "explain_anomaly", "export_report", "export_anomalies_csv",
            "execute_python"
        ],
        "system_prompt": PROMPT_ORQUESTRADOR_ANOMALIAS,
        "status": "ativo",
    },
    {
        "id": "agente_etl",
        "name": "📥 Agente ETL",
        "description": (
            "Especialista em carga e preparação de dados. "
            "Carrega arquivos, avalia qualidade, trata nulos e duplicatas antes da análise."
        ),
        "model": DEFAULT_MODEL_ORQUESTRADOR,
        "provider": DEFAULT_PROVIDER,
        "tools": [
            "importar_dados_reclamacao", "load_data", "load_dataframe_from_json", "list_datasets",
            "preview_data", "assess_data_quality", "auto_clean_data",
        ],
        "system_prompt": PROMPT_AGENTE_ETL,
        "status": "ativo",
    },
    {
        "id": "agente_features",
        "name": "⚙️ Agente Feature Engineer",
        "description": (
            "Especialista em criação de features estratégicas para detecção de anomalias. "
            "Identifica o problema e cria variáveis que amplificam os sinais de irregularidade."
        ),
        "model": DEFAULT_MODEL_ORQUESTRADOR,
        "provider": DEFAULT_PROVIDER,
        "tools": [
            "suggest_features", "apply_high_confidence_features",
            "analyze_for_detection", "execute_python",
        ],
        "system_prompt": PROMPT_AGENTE_FEATURES,
        "status": "ativo",
    },
    {
        "id": "agente_deteccao",
        "name": "🎯 Agente de Detecção",
        "description": (
            "Especialista em aplicação de algoritmos de detecção (Isolation Forest, Z-Score, LOF, Ensemble) "
            "e interpretação dos resultados para auditoria."
        ),
        "model": DEFAULT_MODEL_ORQUESTRADOR,
        "provider": DEFAULT_PROVIDER,
        "tools": [
            "detect_anomalies", "run_full_pipeline", "get_insights",
            "explain_anomaly", "export_anomalies_csv", "export_report", "execute_python",
        ],
        "system_prompt": PROMPT_AGENTE_DETECCAO,
        "status": "ativo",
    },
]


# ============================================================
# PROMPT CONVERSACIONAL (sem tools, sem orquestrador)
# ============================================================
CONVERSATIONAL_PROMPT = """Você é um assistente de auditoria corporativa do Banco Itaú.
Responda de forma clara, objetiva e educada.
Você NÃO possui ferramentas disponíveis neste modo.
Se o usuário quiser executar análises de dados, oriente-o a mencionar um MCP com @, por exemplo:
- @mcp_reclamacao — para análise de reclamações de clientes (inclui consultas SQL no Athena, NLP, séries temporais)
- @mcp_anomalias — para detecção de anomalias em dados transacionais

Você pode responder perguntas gerais, ajudar com conceitos de auditoria, explicar metodologias e orientar o uso do sistema."""


# ============================================================
# MCP SERVERS — lista para autocomplete @mcp no frontend
# ============================================================
MCP_SERVERS = [
    {
        "id": "mcp_reclamacao",
        "name": "📋 MCP Reclamações",
        "icon": "📋",
        "description": "Análise de reclamações: carregamento, filtragem, NLP, lematização, série temporal e exportação.",
        "model": DEFAULT_MODEL,
        "agent_id": "orquestrador",
        "cache_compartilhado": True,
    },
    {
        "id": "mcp_anomalias",
        "name": "🔍 MCP Anomalias",
        "icon": "🔍",
        "description": "Detecção de anomalias: ETL, feature engineering, Isolation Forest, Z-Score, LOF, Ensemble.",
        "model": DEFAULT_MODEL_ORQUESTRADOR,
        "agent_id": "orquestrador_anomalias",
        "cache_compartilhado": True,
    },
]
