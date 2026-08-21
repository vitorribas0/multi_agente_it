# Auditor Multi-Agentes (Playbook Tech Auditors)

Sistema de auditoria assistida por IA: um **backend Django** (motor multi-agente,
tools, integração com o gateway IaraGenAI e Athena) servindo uma API, e um
**front Angular** (`frontend/`) que consome essa API.

> Para rodar você precisa de **dois processos no ar ao mesmo tempo**: o Django
> (porta `8000`) e o Angular (porta `4200`). O front faz proxy de `/api/*` para
> o Django — se o backend estiver desligado, a tela abre mas fica sem dados.

Documentação detalhada do backend em [`documentacao/COMO_RODAR.md`](documentacao/COMO_RODAR.md)
e da arquitetura em [`documentacao/ARQUITETURA_COMPLETA.md`](documentacao/ARQUITETURA_COMPLETA.md).

---

## 1. Pré-requisitos

| Requisito | Detalhe |
|---|---|
| **Python** | 3.11+ (Django 6.0.5) |
| **Node.js** | 18+ com npm (Angular 17) |
| **Credenciais IaraGenAI** | `client_id` + `client_secret` (gateway de LLM) |
| **Credenciais AWS** | access key / secret / session token — para o Athena |
| **CA bundle Itaú** | `arquivos_suporte/cacert.pem` — TLS atrás do proxy corporativo |

---

## 2. Configuração do ambiente (`.env`)

Copie o exemplo e preencha as credenciais:

```bash
cp .env.example .env      # Linux/Mac
copy .env.example .env    # Windows
```

Campos principais (detalhe completo em `documentacao/COMO_RODAR.md` §3):

```ini
IARA_CLIENT_ID=seu_client_id
IARA_CLIENT_SECRET=seu_client_secret
IARA_ENVIRONMENT=homol
IARA_MODEL=gpt-4o

[759242759842_CONSUMER]
aws_access_key_id=SEU_ACCESS_KEY
aws_secret_access_key=SEU_SECRET
aws_session_token=SEU_SESSION_TOKEN

SECRET_KEY=django-insecure-troque-em-producao
DEBUG=True
```

---

## 3. Backend (Django) — terminal 1

```bash
# 1. ambiente virtual
python -m venv .venv
source .venv/bin/activate        # Linux/Mac
.venv\Scripts\activate           # Windows

# 2. dependências
pip install -r requirements.txt

# 3. banco de dados (SQLite; migrations criam agentes, prompts e tools)
python manage.py migrate

# 4. subir o servidor (deixe este terminal aberto)
python manage.py runserver
```

O backend fica em **http://localhost:8000** (a API responde em `/api/...`).

> **Rede corporativa:** se o `pip` não alcançar o PyPI, use o índice do
> Artifactory do Itaú (ver `documentacao/COMO_RODAR.md` §2).

Superusuário para o `/admin` (opcional):

```bash
python manage.py createsuperuser
```

---

## 4. Front-end (Angular) — terminal 2

Em **outro terminal** (deixe o Django rodando no primeiro):

```bash
cd frontend
npm install
npm start          # = ng serve --proxy-config proxy.conf.json
```

O front fica em **http://localhost:4200** e faz proxy de `/api` para o Django
(`proxy.conf.json` → `http://localhost:8000`).

Abra **http://localhost:4200** no navegador.

---

## 5. Verificação rápida

1. Com os **dois** processos no ar, abra http://localhost:4200 → a tela carrega.
2. Envie uma mensagem simples → o orquestrador responde.
3. Faça upload de um CSV → vira o "dataset corrente" da conversa.
4. Abra a Prateleira / Playbooks → devem listar itens vindos da API.

---

## 6. Problemas comuns

| Sintoma | Causa provável | Solução |
|---|---|---|
| Tela abre mas sem dados; console com `ECONNREFUSED` em `/api/...` | **Django desligado** | subir `python manage.py runserver` no terminal 1 |
| `[vite] http proxy error` | front não achou o backend na porta 8000 | idem acima; conferir `frontend/proxy.conf.json` |
| `Falha ao iniciar cliente IaraGenAI` | credenciais IARA ausentes/erradas | conferir `IARA_CLIENT_ID/SECRET/ENVIRONMENT` no `.env` |
| Erro SSL/CA ao consultar Athena | CA bundle não encontrado | garantir `arquivos_suporte/cacert.pem` e as vars `*_CA_BUNDLE` |
| `database is locked` | escrita concorrente no SQLite | processo único; ver notas de deploy em `COMO_RODAR.md` |
| Mudanças não aparecem no navegador | cache do build | hard reload (`Ctrl+Shift+R`) |

---

## 7. Estrutura do projeto

```
auditor/            app Django (models, views, API, motor multi-agente)
auditor_project/    settings/urls do Django
tools/              tools dos agentes (1 arquivo .py por tool, autodiscovery)
prompts/            prompts dos agentes (.md)
frontend/           app Angular (UI que consome a API)
arquivos_suporte/   CA bundle, modelos de OCR, assets de apoio
documentacao/       COMO_RODAR, ARQUITETURA, esquema do sistema
scripts/            utilitários (setup de modelos, etc.)
```

Para adicionar uma tool nova: crie **um** arquivo em `tools/` com o decorator
`@tool` — o autodiscovery registra sozinho. Detalhes em
[`documentacao/COMO_SUBIR_UMA_TOOL.txt`](documentacao/COMO_SUBIR_UMA_TOOL.txt).
