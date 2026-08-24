# Atena — Multi-Agentes de Auditoria

Atena é uma aplicação de auditoria assistida por IA: um **backend Django**
(motor multiagente, tools e Athena) servindo uma API, um worker de execução e
um **front Angular** (`frontend/`) que consome essa API.

Codex é o runtime agente da Atena; OpenAI e Iara são provedores possíveis de
LLM, não nomes da aplicação. O caminho Atena/Codex atual usa OpenAI. As
integrações Iara existentes permanecem restritas ao motor legado e às tools que
já dependiam delas. A decisão arquitetural está detalhada em
[`documentacao/ARQUITETURA_COMPLETA.md`](documentacao/ARQUITETURA_COMPLETA.md).

O Angular é a única interface da aplicação. Django opera como API, Admin e
backend do worker; ele não mantém um frontend paralelo.

> Para rodar você precisa de **três processos no ar ao mesmo tempo**: o Django
> (porta `8000`), o worker da Atena e o Angular (porta `4200`). O front faz proxy
> de `/api/*` para o Django; o worker continua os turnos mesmo se a tela fechar.

Documentação detalhada do backend em [`documentacao/COMO_RODAR.md`](documentacao/COMO_RODAR.md)
e da arquitetura em [`documentacao/ARQUITETURA_COMPLETA.md`](documentacao/ARQUITETURA_COMPLETA.md).

---

## 1. Pré-requisitos

| Requisito | Detalhe |
|---|---|
| **Python** | 3.11 (Django 5.2.17) |
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

## 4. Worker da Atena — terminal 2

Em outro terminal, com o mesmo ambiente virtual ativado:

```bash
python manage.py run_agent_worker
```

O worker consome a fila persistida no banco. Reiniciar o Django ou fechar o
navegador não encerra o turno que ele já está processando.

---

## 5. Front-end (Angular) — terminal 3

Em **outro terminal** (deixe o Django e o worker rodando):

```bash
cd frontend
npm install
npm start          # = ng serve --proxy-config proxy.conf.json
```

O front fica em **http://localhost:4200** e faz proxy de `/api` para o Django
(`proxy.conf.json` → `http://localhost:8000`).

Abra **http://localhost:4200** no navegador.

---

## 6. Verificação rápida

1. Com os **três** processos no ar, abra http://localhost:4200 → a tela carrega.
2. Envie uma mensagem simples → o orquestrador responde.
3. Faça upload de um CSV → vira o "dataset corrente" da conversa.
4. Abra a Prateleira / Playbooks → devem listar itens vindos da API.
5. Num Playbook, conecte duas etapas, declare a saída esperada e clique em
   **Validar** → a ordem real deve aparecer em **Execução e versões**.
6. Selecione o Playbook no chat → o plano deve avançar por etapa e sobreviver
   ao fechamento da tela.

---

## 7. Problemas comuns

| Sintoma | Causa provável | Solução |
|---|---|---|
| Tela abre mas sem dados; console com `ECONNREFUSED` em `/api/...` | **Django desligado** | subir `python manage.py runserver` no terminal 1 |
| Mensagem permanece "na fila" | **Worker desligado** | subir `python manage.py run_agent_worker` no terminal 2 |
| `[vite] http proxy error` | front não achou o backend na porta 8000 | idem acima; conferir `frontend/proxy.conf.json` |
| `Falha ao iniciar cliente IaraGenAI` | credenciais IARA ausentes/erradas | conferir `IARA_CLIENT_ID/SECRET/ENVIRONMENT` no `.env` |
| Erro SSL/CA ao consultar Athena | CA bundle não encontrado | garantir `arquivos_suporte/cacert.pem` e as vars `*_CA_BUNDLE` |
| `database is locked` recorrente | concorrência acima do adequado para SQLite | reiniciar API/worker; em produção usar PostgreSQL |
| Mudanças não aparecem no navegador | cache do build | hard reload (`Ctrl+Shift+R`) |

---

## 8. Estrutura do projeto

```
auditor/            app Django (models, views, API, motor multi-agente)
auditor_project/    settings/urls do Django
tools/              tools dos agentes (1 arquivo .py por tool, autodiscovery)
prompts/            prompts dos agentes (.md)
.agents/skills/      instruções especializadas aplicadas pela Atena/Codex
frontend/           app Angular (UI que consome a API)
arquivos_suporte/   CA bundle, modelos de OCR, assets de apoio
documentacao/       guias atuais de operação e arquitetura
scripts/            utilitários (setup de modelos, etc.)
```

Para adicionar uma tool nova: crie **um** arquivo em `tools/` com o decorator
`@tool` — o autodiscovery registra sozinho. Detalhes em
[`documentacao/COMO_SUBIR_UMA_TOOL.txt`](documentacao/COMO_SUBIR_UMA_TOOL.txt).

Playbooks da interface Angular controlam o worker Atena/Codex: o root contém
regras globais, os demais nós são etapas, as arestas são dependências e cada
salvamento cria uma versão auditável. O worker congela um snapshot no início,
portanto uma edição posterior não altera uma execução já enfileirada.
