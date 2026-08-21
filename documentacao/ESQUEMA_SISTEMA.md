# Esquema Completo do Sistema — Auditor Multi-Agentes

> Plataforma Django de **auditoria técnica assistida por IA**: um orquestrador delega
> tarefas a agentes especialistas, que usam _tools_ (SQL/Athena, análise pandas,
> clustering, OCR de documentos, RAG, gráficos, PDF, busca na web) sobre datasets e
> documentos carregados na conversa. Provider de LLM: **IaraGenAI** (Bedrock/Claude,
> Azure OpenAI/GPT, Vertex/Gemini).

---

## 1. Arquitetura em camadas

```
┌──────────────────────────────────────────────────────────────────────┐
│  FRONTEND (SPA sem framework)                                          │
│  templates auditor/{index,settings,manual}.html                       │
│  static/js/{chat.js, settings.js, main.js}  ·  static/css/style.css   │
└───────────────┬────────────────────────────────────────────────────────┘
                │ HTTP JSON  +  SSE (streaming de progresso)
┌───────────────▼────────────────────────────────────────────────────────┐
│  DJANGO — app "auditor"                                                 │
│  urls.py  →  views.py  (chat, uploads, config, KBs, session-agent)     │
│                     │                                                    │
│                     ▼                                                    │
│  ai_service.py — MOTOR do loop multiagente (run_agent)                 │
│    · resolve provider por modelo   · loop tool-calling (paraleliza)     │
│    · stop/interrupção   · injeção do agente de sessão   · conclusão     │
│                     │                                                    │
│                     ▼                                                    │
│  tools/registry.py — @tool decorator + autodiscover                    │
│    tools/*.py  (17 arquivos, ~35 tools)                                 │
└───────────────┬────────────────────────────────────────────────────────┘
                │ ORM
┌───────────────▼────────────────────────────────────────────────────────┐
│  MODELOS (SQLite db.sqlite3)                                            │
│  AppSettings · Agent · Conversation · SessionAgent · Message · ToolCall │
│  · Knowledge                                                            │
└──────────────────────────────────────────────────────────────────────┘
                │ integrações externas
        ┌───────┴────────┬──────────────┬───────────────┐
     IaraGenAI      AWS Athena     KBs IARA (RAG)     docling/OCR
   (Bedrock/Azure/  (consulta_    (consultar_kb)    (PyMuPDF, RapidOCR)
     Vertex)         aws)
```

---

## 2. Modelo de dados (Django ORM — `auditor/models.py`)

### 2.1 `AppSettings` — singleton de configuração global
| Campo | Tipo | Default | Descrição |
|---|---|---|---|
| `max_iterations` | PositiveInteger | 18 | Máx. de passos com tools por turno de um agente |
| `updated_at` | DateTime (auto_now) | — | |

- Acesso via `AppSettings.get_solo()` (sempre `pk=1`). Limitado a 1–100 no save.

### 2.2 `Agent` — agente global/democratizado (configurável na tela)
| Campo | Tipo | Default | Descrição |
|---|---|---|---|
| `slug` | Slug (único) | — | Identificador (`orquestrador`, `gerador_sql`, …) |
| `name` | Char(80) | — | Nome exibido |
| `description` | Char(240) | "" | |
| `icon` | Char(8) | 🤖 | Emoji |
| `system_prompt` | Text | — | Prompt de sistema |
| `model` | Char(80) | gpt-4o | ID do modelo (ver `MODEL_OPTIONS`) |
| `temperature` | Float | 0.7 | 0.0–2.0 (Claude força 1.0 no motor) |
| `tools_enabled` | JSON (list) | [] | Slugs de tools habilitadas |
| `is_default` | Bool | False | Agente usado quando nenhum é selecionado |
| `created_at` / `updated_at` | DateTime | — | |

- `Meta.ordering = ["-is_default", "name"]`.

### 2.3 `Conversation` — uma conversa de auditoria
| Campo | Tipo | Default | Descrição |
|---|---|---|---|
| `title` | Char(120) | "Nova conversa" | |
| `agent` | FK→Agent (SET_NULL) | null | Agente da conversa (normalmente o orquestrador) |
| `state` | JSON (dict) | {} | **Estado de sessão** compartilhado entre tools (§4) |
| `awaiting_human_input` | Bool | False | True enquanto pausado por `ask_human` |
| `pending_tool_calls` | JSON (list) | [] | Tool calls pendentes ao pausar |
| `created_at` / `updated_at` | DateTime | — | ordering `-updated_at` |

### 2.4 `SessionAgent` — agente criado só para uma conversa (não democratizado)
- `OneToOne`→`Conversation` (related_name `session_agent`); slug reservado `agente_sessao`.

| Campo | Tipo | Default | Descrição |
|---|---|---|---|
| `name` | Char(80) | "Meu agente" | |
| `icon` | Char(8) | 🤖 | |
| `system_prompt` | Text | "" | Prompt do usuário (combinado com boas práticas + guardrails) |
| `model` | Char(80) | gpt-4o | |
| `temperature` | Float | 0.7 | |
| `tools_enabled` | JSON (list) | [] | |
| `guardrails` | Text | "" | Regras que o agente nunca pode violar |
| `documents` | JSON (list) | [] | Docs anexados como markdown: `{filename, markdown, char_count, page_count}` — injetados no prompt |
| `created_at` / `updated_at` | DateTime | — | |

### 2.5 `Message` — mensagem numa conversa
| Campo | Tipo | Default | Descrição |
|---|---|---|---|
| `conversation` | FK→Conversation (CASCADE) | — | related_name `messages` |
| `role` | Char(12) | — | `user` \| `assistant` |
| `content` | Text | "" | |
| `attachment` | JSON (dict) | {} | Anexo único do upload do usuário (tabela/documento) |
| `attachments` | JSON (list) | [] | Cards de artefato do turno (export/chart/mermaid/table) |
| `created_at` | DateTime | — | ordering `created_at` |

### 2.6 `ToolCall` — registro de execução de tool
| Campo | Tipo | Default | Descrição |
|---|---|---|---|
| `message` | FK→Message (CASCADE) | — | related_name `tool_calls` |
| `tool_name` | Char(80) | — | |
| `args` | JSON (dict) | {} | Argumentos passados |
| `result` | Text | "" | Saída da tool |
| `error` | Text | "" | Erro, se houve |
| `duration_ms` | Integer | 0 | |
| `nested_tool_calls` | JSON (list) | [] | Tool calls do sub-agente quando esta é `call_agent` |
| `created_at` | DateTime | — | |

### 2.7 `Knowledge` — conhecimento de especialista/processo (cadastrado na tela)
Prompt reutilizável (política, processo, base de trabalho) que pode ser
selecionado numa conversa. Diferente das KBs (consultadas via tool), o conteúdo
do conhecimento vai **direto no contexto** do turno.

| Campo | Tipo | Default | Descrição |
|---|---|---|---|
| `name` | Char(120) | — | Nome exibido |
| `description` | Char(240) | "" | |
| `icon` | Char(8) | 📚 | Emoji |
| `prompt` | Text | — | Conteúdo injetado no contexto quando ativo |
| `created_at` / `updated_at` | DateTime | — | |

- Na conversa, só o **id** é guardado em `state["active_knowledge"]`; o conteúdo
  é sempre lido do banco em runtime (edições valem na hora). Máx. 10 ativos.

**Relacionamentos:**
```
Agent 1───∞ Conversation 1───∞ Message 1───∞ ToolCall
                    │
                    1───1 SessionAgent
AppSettings (singleton)     Knowledge (independente; referenciado por id no state)
```

---

## 3. Agentes (prompts em `prompts/*.md`, dados iniciais nas migrations)

| Slug | Papel | Delegação |
|---|---|---|
| `orquestrador` | Agente default. Recebe o usuário, planeja e delega via `call_agent`. | Chama todos os demais |
| `gerador_sql` | Gera/executa SQL no Athena (`consulta_aws`, `descrever_tabela`) | — |
| `analista_dados` | Análise tabular (pandas, agrupar, filtrar, gráficos) | — |
| `analista_documentos` | OCR/leitura de documentos (docling) | — |
| `cientista_dados` | Clustering, PCA, outliers, seleção de features | — |
| `agente_sessao` | **SessionAgent** — especialista sob medida da conversa | Alvo do orquestrador via `call_agent` |

O motor injeta em runtime, no orquestrador, uma system-message ensinando a delegar
ao `agente_sessao` quando ele existe (sem editar o prompt democratizado).

---

## 4. Estado de sessão (`Conversation.state` — JSONField)

Dict compartilhado entre tools no mesmo turno. Chaves:

**Persistidas (dados):**
| Chave | Conteúdo |
|---|---|
| `athena_last_result` | Dataset corrente — lista de dicts JSON-safe |
| `athena_last_columns` | Lista de colunas do dataset corrente |
| `athena_last_source` | Origem: `{kind: upload\|batch_docs\|athena, filename/query/...}` |
| `named_datasets` | `{nome: [registros]}` — datasets anteriores preservados (multi-dataset) |
| `documento_atual` | `{filename, markdown, char_count, page_count}` do doc carregado |
| `active_kbs` | KBs RAG selecionadas: `[{id, name, description}]` (máx. 10) |
| `active_knowledge` | Conhecimentos ativos: `[{id}]` — conteúdo lido do banco em runtime (máx. 10) |
| `grounding_chunks` | Fontes retornadas pela busca na web/RAG |

**Controle interno (prefixo `__`, removido antes de persistir):**
`__conversation_id`, `__history`, `__progress` (callback SSE), `__stop_event`,
`__agent_call_depth`, `__awaiting_human`, `__human_question`, `__nested_tool_calls`,
`__pending_attachments`, `__active_knowledge` (conteúdo resolvido dos conhecimentos).

---

## 5. Tools (`tools/*.py`, registradas via `@tool`)

Cada tool declara `slug`, `description`, `icon`, params (inferidos da assinatura +
docstring), `is_human_in_loop`, `uses_session` (se recebe `_session: dict`).

### Delegação & controle
| Tool | Params | Função |
|---|---|---|
| `call_agent` 🤝 | `agent_slug`, `task` | Delega tarefa a sub-agente especialista (serial) |
| `ask_human` ❓ | `question` | **Human-in-loop** — pausa e pergunta ao usuário |
| `thinking` 🧠 | `thought` | Registra raciocínio antes de agir |

### Banco / SQL (Athena)
| Tool | Params | Função |
|---|---|---|
| `consulta_aws` 🗄️ | `query_sql`, `database`, `limit` | SELECT no Athena → vira dataset corrente |
| `descrever_tabela` 🔎 | `tabela`, `database` | Schema (Glue) + preview de 3 linhas |

### Análise tabular (pandas) — `data_analysis.py`
| Tool | Params | Função |
|---|---|---|
| `descrever_dataset` 🔎 | — | Shape, dtypes, nulos, amostra |
| `normalizar_coluna` ✨ | `coluna` | Nova coluna normalizada (lower/sem acento/pontuação) |
| `filtrar_por_termo` 🔬 | `coluna`, `termo`, `modo` | Filtra in-place (contém/não contém) |
| `contar_keywords` 🔠 | `coluna`, `palavras` | Ocorrências por palavra-chave |
| `contem_termo` ✅ | `coluna`, `termo` | Nº linhas, %, exemplos |
| `agrupar` 📊 | `coluna`, `agg`, `coluna_metrica`, `top_n` | Group-by + agregação |
| `regex_extrair` 🧩 | `coluna`, `padrao`, `top_n` | Estatísticas de matches regex |
| `executar_pandas` 🐍 | `codigo` | Código pandas arbitrário (sandbox) — canivete suíço |

### Ciência de dados — `clusterizer.py` / `ds_analise.py`
| Tool | Params | Função |
|---|---|---|
| `executar_kmeans` 🟦 | `n_clusters`, `colunas`, `desenhar` | K-Means → coluna `cluster` |
| `executar_dbscan` 🖧 | `eps`, `min_samples`, `colunas`, `desenhar` | DBSCAN (outliers = -1) |
| `executar_agglomerative` 🌳 | `n_clusters`, `linkage`, `colunas`, `desenhar` | Clustering hierárquico |
| `calcular_silhouette` 📏 | `colunas` | Silhouette score médio |
| `comparar_clusters` 📉 | `k_min`, `k_max`, `colunas`, `desenhar` | Varre K (elbow + silhueta) |
| `avaliar_clusters` 🎯 | `colunas` | Métricas internas + balanceamento |
| `executar_pca` 🧭 | `colunas`, `n_componentes`, `desenhar` | PCA + variância explicada |
| `detectar_outliers` 🚨 | `metodo`, `contaminacao`, `limite`, `colunas`, `desenhar` | Coluna `outlier` (1/0) |
| `selecionar_features` 🧹 | `limiar_correlacao`, `limiar_variancia`, `colunas`, `aplicar` | Remove features redundantes |

### Documentos (OCR / docling) — `document_ocr.py`
| Tool | Params | Função |
|---|---|---|
| `descrever_documento` 📄 | — | Filename, chars, páginas |
| `ler_documento` 📖 | `offset`, `tamanho` | Trecho em markdown (default 4000 chars) |
| `buscar_no_documento` 🔎 | `termo`, `top_n` | Ocorrências com contexto |
| `extrair_tabelas_do_documento` 📊 | `top_n` | Tabelas detectadas em markdown |

### GenAI / RAG / Web
| Tool | Params | Função |
|---|---|---|
| `analise_massiva_llm` 🚀 | `coluna_texto`, `colunas_saida`, `contexto`, `modelo`, `limite`, `confirmado` | Classifica cada linha via LLM → N colunas |
| `consultar_kb` 📚 | `consulta`, `top_k` | Similarity search nas KBs IARA ativas |
| `buscar_na_web` 🌐 | `consulta` | Pesquisa web atual (via Vertex/grounding) |

### Saídas / artefatos
| Tool | Params | Função |
|---|---|---|
| `gerar_grafico` 📈 | `tipo`, `categorias`, `valores`, `series_json`, `x`, `y`, `matriz_json`, `bins`, `titulo`, … | Gráfico matplotlib (barras/linha/pizza/dispersão/histograma/boxplot/heatmap) → card PNG |
| `gerar_fluxograma` 🗺️ | `mermaid`, `titulo` | Fluxograma Mermaid → card (PNG/SVG/.mmd) |
| `gerar_documentacao_pdf` 📕 | `markdown`, `titulo`, `subtitulo`, `nome_arquivo` | PDF estilizado → card de download |
| `exportar_dataset` 💾 | `formato`, `nome` | Exporta dataset corrente (CSV/Excel) → download_url |

Artefatos são publicados via `publish_attachment()` em `__pending_attachments` e
drenados ao persistir o turno (viram `Message.attachments`).

---

## 6. Motor multiagente (`ai_service.py` — `run_agent`)

Fluxo de um turno:
1. Deriva o **provider** pelo ID do modelo (`_provider_for`): `bedrock` (claude/anthropic/llama/nova/mistral/…), `azure_openai` (gpt/o1/o3/o4), `vertex` (gemini).
2. Monta `messages`: system_prompt + (injeção do agente de sessão, se orquestrador) + histórico + mensagem nova. Injeta catálogo de KBs ativas e habilita `consultar_kb` automaticamente.
3. Loop até `max_iterations`:
   - Chama `client.chat.completions.create` (Claude → `thinking: adaptive`, `effort: high`, `max_tokens 64000`, `temperature 1.0`).
   - Sem tool_calls → resposta final.
   - `ask_human` entre as calls → **pausa** (awaiting_human) antes de executar as demais.
   - `call_agent` roda **serialmente** (compartilha estado); demais tools em **paralelo** (`ThreadPoolExecutor`, máx. 6).
   - Anexa resultados na ordem original e re-chama o modelo.
4. Esgotou iterações com trabalho feito → chamada final com `tool_choice="none"` forçando conclusão a partir dos resultados.

**Interrupção (stop):** cada turno em streaming registra um `threading.Event` por conversa (`register_stop`/`request_stop`/`clear_stop`); o loop checa a cada iteração e encerra preservando resultados parciais.

**`MODEL_OPTIONS`** (tela de config): Claude (opus-4-6, opus-4-8, sonnet-4-5, sonnet-4), GPT (5.2, 5.1, 5, 5-mini, 4.1, 4.1-mini, 4o), Gemini (2.5-pro, 2.5-flash, 2.5-flash-lite).

---

## 7. API HTTP (`auditor/urls.py`)

**Páginas:** `/` (index), `/manual/`, `/settings/`

**Chat:**
- `POST /api/chat/` — turno síncrono (`chat_message`)
- `POST /api/chat/stream/` — turno em **SSE** com progresso ao vivo (`chat_stream`)
- `POST /api/conversations/<id>/stop/` — interrompe geração

**Conversas:**
- `GET /api/conversations/` — lista
- `GET /api/conversations/<id>/` — detalhe (mensagens + tool calls)
- `PATCH\|POST .../rename/` · `DELETE .../delete/`
- `GET .../dataset/?offset&limit` — pagina o dataset corrente (sem LLM)

**Knowledge Bases (RAG):**
- `GET /api/kbs/?refresh=1` — lista KBs IARA (cache 5 min)
- `GET\|POST\|PUT /api/conversations/<id>/kbs/[save/]` — KBs ativas da conversa

**Agente da sessão:**
- `GET\|POST\|DELETE /api/conversations/<id>/session-agent/[save/|delete/]`
- `POST /api/session-agent/create-conversation/` — cria conversa já com agente
- `POST /api/session-agent/extract-document/` — extrai markdown de um doc (sem persistir)

**Uploads:**
- `POST /api/upload/` — CSV/XLSX (tabela) ou PDF/DOCX/imagem (documento via docling) → dataset/documento na sessão
- `POST /api/upload-batch/` — vários PDFs/TXTs → dataset `[nome_arquivo, conteudo_extraido]`

**Downloads:**
- `GET /api/exports/<filename>` — serve `exports/` (regex anti path-traversal, csv/xlsx/pdf)

**Configuração:**
- `GET /api/config/` — agentes + `MODEL_OPTIONS` + tools + settings
- `POST /api/config/settings/` — salva `max_iterations`
- `POST /api/config/agents/<slug>/` — atualiza um agente

---

## 8. Ingestão de arquivos

| Tipo | Extensões | Pipeline | Vira |
|---|---|---|---|
| Tabela | `.csv .xlsx .xls` | pandas (`_read_table`) | dataset corrente (`athena_last_*`) |
| Documento | `.pdf .docx .pptx .html .md .txt .png .jpg .tiff …` | **docling** + OCR (RapidOCR ONNX, offline) | `documento_atual` |
| Lote | `.pdf .txt` | PyMuPDF (`fitz`) / texto | dataset `[nome_arquivo, conteudo_extraido]` |
| Doc do agente | `.pdf .txt .md .docx .doc` | docling / texto puro | `SessionAgent.documents` (injetado no prompt) |

Limites: 25 MB por arquivo, 50 000 linhas processadas, 3 linhas para o LLM, 100 na UI.

---

## 9. Configuração / infra

- **Django 6.0**, app único `auditor`, projeto `auditor_project`. Banco **SQLite** (`db.sqlite3`).
- `.env` (via `python-dotenv`): `IARA_CLIENT_ID/SECRET`, `IARA_ENVIRONMENT`, `IARA_PROVIDER`, `IARA_MODEL_*`, `IARA_ACCESS_TOKEN`, credenciais AWS/Athena.
- `auditor/proxy_config.py` — configura proxy corporativo e `artifacts_path` do docling (modelos offline em `arquivos_suporte/docling/`).
- 29 migrations (evolução: agentes, tools, modelos Claude/OpenAI, session agent, app settings, buscar_na_web, knowledge).
- `restart.sh`, `manage.py`, `scripts/` (setup de modelos docling/IARA). Diretório `layout/` = esqueleto Django alternativo (não é o app ativo).
