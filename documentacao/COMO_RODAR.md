# 🚀 Como rodar a aplicação — Auditor Multi-Agentes

Guia de setup e execução do sistema Django de auditoria assistida por IA.

---

## 1. Pré-requisitos

| Requisito | Detalhe |
|---|---|
| **Python** | 3.11+ (o projeto usa Django 6.0.5) |
| **Ambiente** | SageMaker (Linux) ou máquina corporativa Windows/Itaú |
| **Credenciais IaraGenAI** | `client_id` + `client_secret` (LLM gateway) |
| **Credenciais AWS** | Access key / secret / session token — para consultar o Athena |
| **CA bundle Itaú** | `arquivos_suporte/cacert.pem` — para TLS atrás do proxy corporativo |
| **Modelos Docling** (opcional) | OCR offline de PDFs/imagens (ver §6) |

---

## 2. Instalação

```bash
# 1. Clonar e entrar no projeto
cd itau-hx9-modules-playbook-tech-auditors

# 2. Criar e ativar o ambiente virtual
python -m venv .venv
source .venv/bin/activate        # Linux/SageMaker
# .venv\Scripts\activate         # Windows

# 3. Instalar dependências
pip install -r requirements.txt
```

> **Rede corporativa:** se o `pip` não alcançar o PyPI público, use o índice do
> Artifactory do Itaú:
> ```bash
> pip install -r requirements.txt \
>   --index-url https://artifactory.prod.aws.cloud.ihf/artifactory/api/pypi/python-devel/simple \
>   --trusted-host artifactory.prod.aws.cloud.ihf
> ```

---

## 3. Configuração (`.env`)

Copie `.env.example` para `.env` e preencha:

```ini
# ── IaraGenAI (LLM gateway) ──────────────────────────────
IARA_CLIENT_ID=seu_client_id
IARA_CLIENT_SECRET=seu_client_secret
IARA_ENVIRONMENT=homol
IARA_PROVIDER=azure_openai          # bedrock | azure_openai | vertex
IARA_MODEL=gpt-4o
IARA_ACCESS_TOKEN=seu_access_token

# ── AWS / Athena ─────────────────────────────────────────
[759242759842_CONSUMER]
aws_access_key_id=SEU_ACCESS_KEY
aws_secret_access_key=SEU_SECRET
aws_session_token=SEU_SESSION_TOKEN

AWS_DEFAULT_REGION=sa-east-1
AWS_CA_BUNDLE=arquivos_suporte/cacert.pem
REQUESTS_CA_BUNDLE=arquivos_suporte/cacert.pem
CURL_CA_BUNDLE=arquivos_suporte/cacert.pem

# ── Django ───────────────────────────────────────────────
SECRET_KEY=django-insecure-troque-em-producao
DEBUG=True

# ── Proxy corporativo (Windows/Itaú; deixe vazio no SageMaker) ──
CORP_PROXY=proxynew.itau:8080
NO_PROXY=localhost,127.0.0.1,::1,.itau,.cloud.itau.com.br
```

> **Provider é derivado do modelo em runtime.** Trocar o modelo na tela de
> Configurações não exige mexer no `.env` — `_provider_for()` escolhe
> bedrock/azure/vertex pelo prefixo do id (`anthropic.*` → bedrock, `gpt*` →
> azure, `gemini*` → vertex).

Variáveis opcionais reconhecidas pelo motor: `IARA_MODEL_DEFAULT`,
`IARA_MODEL_ORQUESTRADOR`, `IARA_MODEL_MASSIVA` (default `gpt-4o-mini` para a
análise massiva).

---

## 4. Banco de dados

O banco é **SQLite** (`db.sqlite3`), já com os agentes democratizados
configurados via migrations (prompts, tools habilitadas, modelos).

```bash
python manage.py migrate
```

> As migrations `0001`…`0029` recarregam os prompts de `prompts/*.md` e a
> configuração de cada agente — o banco é a **fonte da verdade** da config dos
> agentes.

Criar um superusuário (opcional, para o `/admin` e editar agentes por lá):

```bash
python manage.py createsuperuser
```

---

## 5. Rodar o servidor

**Modo direto:**

```bash
python manage.py runserver 0.0.0.0:8000
```

**Via script (mata a instância anterior e reinicia):**

```bash
./restart.sh
```

Acesse:

- **Local:** http://localhost:8000
- **SageMaker:** `https://<seu-host>.studio.sa-east-1.sagemaker.aws/codeeditor/default/ports/8000/`

### Rotas principais

| Página | URL |
|---|---|
| Chat | `/` |
| Manual | `/manual/` |
| Configurações (agentes, modelos, tools) | `/settings/` |
| Django Admin | `/admin/` |

---

## 6. OCR de documentos (Docling) — opcional

O upload de PDF/DOCX/imagem é extraído para markdown via **Docling** com OCR
offline (RapidOCR ONNX). Os modelos de layout/tabela ficam em
`arquivos_suporte/docling/`.

```bash
python scripts/setup_docling_models.py
```

Sem os modelos locais, o Docling tenta baixar do HuggingFace (bloqueado na rede
Itaú sem proxy). Documentos **com texto nativo** continuam funcionando via
fallback; PDFs escaneados/imagens exigem os modelos.

---

## 7. Verificação rápida

1. Abra `/` e envie uma mensagem simples → o orquestrador deve responder.
2. Faça upload de um CSV → deve virar o "dataset corrente" da conversa.
3. Peça uma análise ("descreva o dataset") → o orquestrador delega para o
   `analista_dados`.
4. Abra `/settings/` → confira agentes, modelos disponíveis e tools habilitadas.

---

## 8. Problemas comuns

| Sintoma | Causa provável | Solução |
|---|---|---|
| `Falha ao iniciar cliente IaraGenAI` | credenciais IARA ausentes/erradas | conferir `IARA_CLIENT_ID/SECRET/ENVIRONMENT` no `.env` |
| Erro SSL/CA ao consultar Athena | CA bundle não encontrado | garantir `arquivos_suporte/cacert.pem` e as vars `*_CA_BUNDLE` |
| `database is locked` | escrita concorrente no SQLite | ver `melhorias/opniao_claude.md` (Tier 1.1 — WAL/Postgres) |
| Docling não extrai imagem | modelos OCR ausentes | rodar `scripts/setup_docling_models.py` |
| `docling não está instalado` | dependência faltando | instalar `docling rapidocr_onnxruntime` (ver §2) |
| Página HTML de erro em vez de JSON | `DEBUG=True` e exceção não tratada | ler o traceback; a maioria dos endpoints já devolve JSON |

---

## 9. Notas de deploy

- O sistema hoje pressupõe **processo único** (o registro de "stop" das gerações
  vive em memória por processo — `_STOP_EVENTS`). Rodar com Gunicorn
  multi-worker quebra o botão de parar. Ver `melhorias/opniao_claude.md` (Tier 3.5).
- `DEBUG=True` e `SECRET_KEY` hardcoded no `settings.py` **não** devem ir para
  produção sem ajuste.
- Para escalar usuários simultâneos, planejar a migração de SQLite → PostgreSQL
  (ver `melhorias/opniao_claude.md`, Tier 1.1).

---

## 10. Como adicionar uma nova tool

Resumo: criar **um** arquivo `.py` em `tools/` com o decorator `@tool` — o
autodiscovery registra sozinho. Detalhes completos em
[`../COMO_SUBIR_UMA_TOOL.txt`](../COMO_SUBIR_UMA_TOOL.txt) e na arquitetura
([`ARQUITETURA_COMPLETA.md`](ARQUITETURA_COMPLETA.md), §5).
