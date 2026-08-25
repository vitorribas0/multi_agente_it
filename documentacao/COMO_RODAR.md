# Como rodar a Atena

A aplicação possui três processos independentes: Angular, API Django e worker
da Atena. O Django não entrega páginas da aplicação; seu papel é API, Admin e
execução de tarefas de backend.

## Pré-requisitos

- Python 3.11 ou superior.
- Node.js 18 ou superior.
- `OPENAI_API_KEY` para o agente principal.
- Credenciais Iara e AWS somente para as funcionalidades legadas que as utilizam.

## Instalação

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cd frontend
npm install
cd ..
```

O `requirements.txt` instala `openai-codex` e seu runtime fixado. Não instale o
Codex CLI ou o ChatGPT Desktop para executar a Atena: o worker utiliza somente
a versão empacotada com o projeto.

Copie `.env.example` para `.env`, preencha apenas as credenciais necessárias e
nunca versione o arquivo resultante.

```ini
OPENAI_API_KEY=sk-...
ATENA_CODEX_MODEL=gpt-5.6-terra
ATENA_CODEX_REASONING_EFFORT=medium
```

O primeiro turno registra essa chave em `runtime/codex_home/auth.json`, com o
diretório inteiro ignorado pelo Git. Quando a chave mudar, o Atena atualiza a
autenticação automaticamente. Para escolher outro local persistente, configure
`ATENA_CODEX_HOME`.

## Banco local

```bash
python manage.py migrate
```

O desenvolvimento local usa `db.sqlite3`. API e worker compartilham esse banco;
o timeout de escrita está ampliado para reduzir contenção. PostgreSQL será o
backend recomendado para execução distribuída e ECS.

## Iniciar os serviços

Terminal 1 — API:

```bash
source .venv/bin/activate
python manage.py runserver 127.0.0.1:8000
```

Terminal 2 — worker persistente:

```bash
source .venv/bin/activate
python manage.py run_agent_worker
```

Terminal 3 — Angular:

```bash
cd frontend
npm start -- --host 127.0.0.1
```

Abra [http://127.0.0.1:4200](http://127.0.0.1:4200). O proxy do Angular envia
somente `/api/*` para `http://127.0.0.1:8000`.

## Endereços

| Componente | Endereço |
|---|---|
| Chat Angular | `http://127.0.0.1:4200/chat` |
| Configurações Angular | `http://127.0.0.1:4200/settings` |
| Playbooks Angular | `http://127.0.0.1:4200/playbooks` |
| API Django | `http://127.0.0.1:8000/api/...` |
| Django Admin | `http://127.0.0.1:8000/admin/` |

As antigas páginas Django `/`, `/manual/` e `/settings/` não fazem parte da
aplicação. A rota `/settings` usada pelo usuário pertence ao Angular na porta
4200.

## Verificação rápida

```bash
python manage.py check
python manage.py test auditor.tests
curl http://127.0.0.1:8000/api/codex/status/
curl http://127.0.0.1:4200/api/codex/status/
```

Na interface, envie uma mensagem, recarregue a página durante a execução e
confirme que o acompanhamento retorna. Teste também uma pergunta interativa e o
botão de parar.

## Problemas comuns

| Sintoma | Causa provável | Ação |
|---|---|---|
| Angular abre sem dados | API desligada | iniciar `runserver` |
| Mensagem permanece na fila | worker desligado | iniciar `run_agent_worker` |
| `ECONNREFUSED` no proxy | porta 8000 indisponível | verificar o processo Django |
| `database is locked` recorrente | concorrência excessiva no SQLite | reiniciar API/worker; migrar para PostgreSQL |
| Codex indisponível | SDK ou credencial ausente | reinstalar `requirements.txt` e revisar `OPENAI_API_KEY` |

## Produção

- Angular deve ser publicado como aplicação estática separada.
- Django deve rodar como serviço de API.
- O worker deve rodar em processo ou task ECS independente.
- PostgreSQL/RDS substitui SQLite.
- S3 substitui o filesystem local dos chats.
- Secrets Manager fornece credenciais; segredos nunca entram na imagem.
- `ATENA_CODEX_HOME` deve apontar para um volume persistente do worker; ele não
  é a pasta pessoal de nenhum usuário.
