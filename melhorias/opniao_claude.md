# 🧭 Opinião Técnica — Auditor Multi-Agentes

> **Autor:** Claude (Opus 4.8) · **Data:** 2026-07-21
> **Base da análise:** leitura estática de `ai_service.py`, `views.py`,
> `tools/registry.py`, `call_agent.py`, `executar_pandas.py`, `consulta_aws.py`,
> `analise_massiva.py`, `clusterizer.py`, `ARQUITETURA.md`, `ESQUEMA_SISTEMA.md`
> e `MELHORIAS_FUTURAS.txt`.

---

## Resumo executivo

A arquitetura é **muito boa** para um projeto interno: separação limpa
(registry / motor / views), streaming SSE bem feito, stop cooperativo,
human-in-the-loop e delegação com árvore de tool calls. Os pontos abaixo são de
**robustez e escala** — não de concepção. Estão priorizados por impacto.

> **Nota sobre as melhorias futuras já existentes:** este documento **incorpora e
> substitui** o `MELHORIAS_FUTURAS.txt`. Dois itens de lá foram verificados no
> código e **já estão resolvidos** — portanto foram removidos:
> - ~~N+1 ao carregar mensagens + tool calls~~ → resolvido (`prefetch_related("tool_calls")`
>   em `conversation_detail`; `select_related` em `conversation_list`).
> - ~~MAX_ITERATIONS + conclusão forçada~~ → resolvido (18 iterações + chamada final
>   com `tool_choice="none"`).
>
> Os itens ainda abertos de lá (janela de contexto, tool results não reenviados,
> clustering sobre texto livre) estão integrados nas seções abaixo.

---

## 🔴 Tier 1 — O que pode QUEBRAR (atacar primeiro)

### 1.1 · SQLite + threads worker + escrita concorrente → `database is locked`
Cada turno em streaming sobe uma thread (`chat_stream` → `worker()`) que grava
`Message` / `ToolCall` / `state`. Com 2–3 auditorias simultâneas você bate em
*"database is locked"*. É o mais provável de derrubar o sistema em uso real.

- **Mínimo:** WAL mode + `timeout` no `OPTIONS` do banco.
- **Estratégico:** migrar para **PostgreSQL** antes de escalar usuários.

### 1.2 · `consulta_aws` NÃO valida "somente SELECT" — só o prompt promete
A descrição diz que `DELETE/DROP/UPDATE/INSERT` são proibidos, mas o código
(`consulta_aws.py:94-111`) só mexe no `LIMIT`. **Não há nenhuma checagem.** A
única barreira é o LLM obedecer o prompt e as permissões do workgroup Athena. Se
as credenciais permitirem CTAS/INSERT/DROP, um prompt malicioso ou erro do modelo
executa.

- **Correção:** bloquear se a query normalizada não começar com `SELECT`/`WITH`;
  rejeitar `;` múltiplos e keywords DDL/DML.

### 1.3 · `executar_pandas` — o sandbox dá falsa sensação de segurança
O blocklist por regex bloqueia `import`/`open`/`exec`/`dunder`, mas `pd` e `np`
estão injetados. `pd.read_csv('/etc/passwd')`, `pd.read_pickle(url)` (**RCE via
pickle!**), `df.to_csv('/qualquer/lugar')` — tudo passa sem `import`. Leitura e
escrita arbitrária de arquivos e potencial execução remota de código.

- **Consciência:** para público interno confiável talvez seja tolerável.
- **Mitigação real:** subprocesso isolado/container, ou ao menos bloquear
  `read_pickle`/`to_pickle`/`read_*` com path e restringir `pd.eval`.

### 1.4 · Estado compartilhado + tools em paralelo = corrida no dataset
O motor roda até 6 tools em paralelo (`ThreadPoolExecutor`) compartilhando o
**mesmo `session` dict mutável**. Só `call_agent` é serializado. Se o modelo
emitir no mesmo turno duas tools que escrevem o dataset (`executar_pandas` com
`result_df`, `normalizar_coluna`, `filtrar_por_termo`…), elas fazem
read-modify-write concorrente em `session["athena_last_result"]` → **resultado
não-determinístico / dataset corrompido**.

- **Correção:** serializar também as tools que mutam o dataset (marcar
  `mutates_session` no `ToolSpec` e rodá-las em série), ou lock por chave de sessão.
- 🆕 *Gap não capturado no `MELHORIAS_FUTURAS.txt` original.*

### 1.5 · `_setup_aws_env()` muta `os.environ` global dentro de threads
`consulta_aws.py:16` seta `HTTP_PROXY`/`HTTPS_PROXY`/`AWS_CA_BUNDLE` no processo
inteiro, a cada chamada, de dentro de threads. É global e afeta **todas** as
conexões do processo (inclusive chamadas IaraGenAI que talvez não devessem ir
pelo proxy), com corrida entre threads.

- **Correção:** configurar proxy/CA via sessão boto3 dedicada, não via
  `os.environ` global.

### 1.6 · Stop não interrompe tool longa
O `stop_event` é checado só nas bordas do loop. Uma `analise_massiva_llm` de
8.000 linhas ou uma query Athena pesada **ignora o stop** até terminar. O usuário
clica "parar" e nada acontece por minutos.

- **Correção:** passar o `stop_event` para dentro das tools longas (a massiva já
  tem `__progress`; dá pra checar stop no mesmo laço `as_completed`).

### 1.7 · Zero retry/backoff nas chamadas de LLM
`client.chat.completions.create` sem resiliência para 429/5xx transitório → um
soluço da IARA derruba o turno inteiro com mensagem genérica.

- **Correção:** retry com backoff exponencial em torno das chamadas do modelo.

---

## 🟡 Tier 2 — Memória / Datasets (gargalo estrutural)

### 2.1 · Histórico sem janela (maior ROI)
`views.py:514` e `:574`: `history = todas as mensagens`. Sem janela, sem
sumarização. Custo e latência crescem linearmente; eventualmente estoura o
contexto do modelo.

- **Proposta:** manter as N últimas mensagens na íntegra + resumo das antigas
  (sumarização incremental); no mínimo, corte por orçamento (nº de mensagens /
  total de caracteres), preservando system prompt e mensagens recentes. Marcar
  mensagens de upload (blocos JSON grandes) para compressão preferencial.
- *(Era o item 1 do `MELHORIAS_FUTURAS.txt` — mantido, ainda aberto.)*

### 2.2 · Dataset inteiro vive dentro de `Conversation.state` no SQLite
Até 50.000 linhas de dicts JSON ficam na coluna `state`. Cascata:

- Toda vez que carrega a conversa, o dataset inteiro entra na memória.
- `dict(conv.state or {})` a cada turno copia tudo.
- `_persist_turn` regrava tudo no SQLite quando `state_changed`.
- **`named_datasets` só cresce** — cada nova query/upload empurra o dataset
  anterior pra dentro do `state` e **nunca poda** (`consulta_aws.py:126`,
  `views.py:1499`). Auditoria longa acumula vários datasets de 50k linhas no
  mesmo registro.

- **Proposta:** mover datasets para fora do `state` (parquet em disco/S3, ou
  tabela própria) guardando só um handle; pôr **teto/expiração** no
  `named_datasets`.
- 🆕 *Gap não capturado no `MELHORIAS_FUTURAS.txt` original.*

### 2.3 · Resultados de tool de turnos passados somem
O histórico só carrega `role` + `content`; os resultados das `ToolCall` de turnos
passados NÃO voltam ao modelo. Trade-off consciente (contém o contexto), mas
combinado com o 2.1 gera re-execução de tools.

- **Proposta:** reinserir um RESUMO curto dos resultados de tool mais relevantes
  (não o payload inteiro), equilibrando com o 2.1 — **só depois** de resolver a
  janela.
- *(Era o item 3 do `MELHORIAS_FUTURAS.txt` — mantido, ainda aberto.)*

---

## 🟠 Tier 3 — Orquestração & Agentes

### 3.1 · Temperatura configurada dos agentes Claude é ignorada
`_build_completion_kwargs` força `temperature=1.0` para todo Claude (por causa do
thinking). A tabela da `ARQUITETURA.md` mostra orquestrador temp 0.0,
`gerador_sql` 0.2 etc. — esses valores **não têm efeito** nos agentes Claude (que
são todos hoje). Não é bug, mas é expectativa falsa.

- **Ação:** documentar/ocultar o campo de temperatura para modelos Claude.

### 3.2 · Orçamento de iterações não é conjunto entre pai e sub-agentes
`_max_iterations()` é global; cada `call_agent` roda um `run_agent` com as 18
iterações inteiras de novo. Com `MAX_DEPTH=3`, o pior caso é explosão de
custo/latência (18 × cadeia de sub-agentes) sem teto agregado.

- **Ação:** orçamento de tokens/iterações propagado e decrementado na sessão.

### 3.3 · Sub-agente não vê o trabalho do turno atual do pai
`call_agent` passa `__history` (turnos anteriores) + a `task`, mas **não** as
mensagens intermediárias do turno corrente (tool calls que o orquestrador já fez
agora). O especialista pode começar "cego" do que acabou de ser apurado. Às vezes
o orquestrador compensa colocando tudo na `task`, mas é frágil.

### 3.4 · Retomada do human-in-the-loop perde o contexto de tool-calling
Quando `ask_human` pausa, `_persist_turn` salva a mensagem do assistente como
texto (`content=human_question`) e `pending_tool_calls` **não é preenchido** no
fluxo. Na resposta do usuário, o próximo turno remonta `messages` só de
`role`/`content` — as tool_calls originais não voltam. O modelo **re-planeja do
zero** em vez de retomar onde parou. Funciona na prática, mas contradiz o que o
modelo de dados (`pending_tool_calls`) sugere.

- **Ação:** alinhar código e intenção (persistir/restaurar as tool_calls pendentes).
- 🆕 *Gap não capturado no `MELHORIAS_FUTURAS.txt` original.*

### 3.5 · `_STOP_EVENTS` é in-memory por processo
Com Gunicorn multi-worker, o `/stop` cai num processo que não tem o event daquela
conversa → stop silenciosamente não funciona. Amarra o deploy a single-process.

- **Ação:** documentar a restrição de deploy (ou mover para backend compartilhado).

---

## 🟢 Tier 4 — Tools & Limpeza

### 4.1 · `analise_massiva` cria um `IaraGenAI` novo por linha
`analise_massiva.py:66` — até 8.000 instanciações + provável fetch de token a
cada uma. Reutilizar um client por worker/thread reduz muito overhead.

### 4.2 · Clustering sobre texto livre
As tools de clustering usam apenas colunas numéricas
(`clusterizer.py::_select_features` → `select_dtypes(number)`). Em datasets de
incidentes, as colunas relevantes (Descrição, Causa, Solução, Sintoma, Categoria)
são texto livre / categóricas — o clustering roda sobre pouca ou nenhuma feature.

- **Proposta:** pré-processar texto antes de clusterizar (TF-IDF ou embeddings das
  descrições + one-hot das categóricas) e clusterizar sobre essa matriz.
- *(Era o item 5 do `MELHORIAS_FUTURAS.txt` — mantido, ainda aberto.)*

### 4.3 · `layout/` é um segundo esqueleto Django morto
Confunde quem chega. A própria doc admite que não é o app ativo. **Remover.**

### 4.4 · `.venv/pyvenv.cfg` versionado no git
Venv não deveria ir para o repositório.

### 4.5 · Duplicação de `_provider_for`
Uma em `ai_service.py`, outra em `analise_massiva.py` — extrair para módulo comum
evita divergência futura.

---

## ✅ Checklist por Tier

### 🔴 Tier 1 — Pode quebrar (urgente)
- [ ] 1.1 · Ativar WAL + `timeout` no SQLite; planejar migração para PostgreSQL
- [ ] 1.2 · Validar SELECT-only na `consulta_aws` (bloquear DDL/DML e `;` múltiplos)
- [ ] 1.3 · Endurecer sandbox do `executar_pandas` (bloquear `read_pickle`/`read_*` com path; avaliar subprocesso isolado)
- [ ] 1.4 · Serializar tools que mutam o dataset (flag `mutates_session` ou lock por sessão) 🆕
- [ ] 1.5 · Configurar proxy/CA via sessão boto3 dedicada (parar de mutar `os.environ` global)
- [ ] 1.6 · Propagar `stop_event` para dentro das tools longas (massiva, Athena)
- [ ] 1.7 · Retry com backoff exponencial nas chamadas de LLM

### 🟡 Tier 2 — Memória / Datasets
- [ ] 2.1 · Janela + sumarização incremental do histórico *(maior ROI)*
- [ ] 2.2 · Mover datasets para fora do `state`; teto/expiração no `named_datasets` 🆕
- [ ] 2.3 · Reinserir resumo curto de resultados de tool relevantes *(após 2.1)*

### 🟠 Tier 3 — Orquestração & Agentes
- [ ] 3.1 · Documentar/ocultar temperatura para modelos Claude
- [ ] 3.2 · Orçamento de iterações/tokens conjunto entre pai e sub-agentes
- [ ] 3.3 · Expor ao sub-agente o trabalho do turno atual do pai
- [ ] 3.4 · Alinhar retomada do human-in-the-loop com `pending_tool_calls` 🆕
- [ ] 3.5 · Documentar restrição single-process do `_STOP_EVENTS` (ou backend compartilhado)

### 🟢 Tier 4 — Tools & Limpeza
- [ ] 4.1 · Reutilizar client `IaraGenAI` por worker na `analise_massiva`
- [ ] 4.2 · Clustering sobre texto livre (TF-IDF/embeddings + one-hot)
- [ ] 4.3 · Remover o esqueleto morto `layout/`
- [ ] 4.4 · Tirar `.venv/` do versionamento
- [ ] 4.5 · Extrair `_provider_for` para módulo comum

### ✔️ Já resolvido (removido das melhorias futuras)
- [x] N+1 ao carregar mensagens/tool calls → `prefetch_related` + `select_related`
- [x] MAX_ITERATIONS + conclusão forçada → 18 iterações + chamada final `tool_choice="none"`

---

## Sequência recomendada (estratégica)

| # | Ação | Por quê |
|---|------|---------|
| 1 | Janela/sumarização de histórico (2.1) | maior ROI: custo, latência e teto de contexto de uma vez |
| 2 | WAL + timeout no SQLite, plano de Postgres (1.1) | é o que derruba o sistema com concorrência |
| 3 | Validar SELECT-only na `consulta_aws` (1.2) | risco de dado real, correção barata |
| 4 | Serializar tools que mutam dataset (1.4) | corrupção silenciosa é a pior classe de bug de auditoria |
| 5 | Tirar dataset do `state` + podar `named_datasets` (2.2) | remove o gargalo estrutural de memória |
| 6 | Stop dentro de tools longas + retry de LLM (1.6, 1.7) | robustez percebida pelo usuário |
