# Alterações do snapshot funcional no ambiente Itaú

Este documento registra as diferenças importadas do `zip.json` fornecido para a
branch `codex-itau-compat-24-08`. O snapshot foi comparado com o commit
`5bc7f03`, que era o estado da branch `codex-glass-preview` no início do
trabalho.

## Resultado

O snapshot conecta o caminho Atena/Codex ao SDK IARA por meio de um adaptador
HTTP compatível com a Responses API. O restante da arquitetura continua igual:
Angular, Django, worker, banco, playbooks, skills, sandbox, aprovações e
perguntas ao usuário.

```text
Angular → Django → worker → Codex App Server
                              ↓
                  adaptador Responses local
                              ↓
                      SDK corporativo IARA
```

## Alterações importadas

- `auditor/iara_adapter.py`: adaptador `/v1/responses`, SSE, autenticação local,
  erros e integração com `client.responses` do SDK.
- `auditor/codex_app_server.py`: habilitação por `ATENA_IARA_ENABLED`, geração
  do `config.toml` próprio do Codex e inicialização do adaptador.
- `auditor/management/commands/iara_status.py`: diagnóstico sem imprimir
  valores de credenciais.
- `auditor/worker_lock.py`: trava do worker compatível com Unix e Windows.
- `auditor/codex_views.py` e `run_agent_worker.py`: uso da trava
  multiplataforma.
- `.env.example`: documentação das variáveis IARA e do adaptador, somente com
  placeholders.
- `testes/iara/`: diagnósticos, testes offline e testes reais opt-in.
- `readm_implementar_iara.md`: resultado técnico da homologação realizada no
  snapshot.

## Remoções necessárias

Foi removido o diretório versionado `iaragenai/`. Ele não era o SDK do Itaú:
era um shim local que usava a API OpenAI e, por ter o mesmo nome do pacote
corporativo, impedia o Python de importar o SDK IARA real.

Também foi removido `tmp_test_models.py`, um diagnóstico temporário que dependia
desse shim.

## Segredos

O `zip.json` continha um `.env` preenchido. Esse arquivo foi deliberadamente
excluído da importação e continua protegido pelo `.gitignore`. Nenhuma
credencial do snapshot deve ser copiada para código, documentação, commit ou
frontend.

## Ajuste feito durante a importação

O snapshot criava `auditor/tests/` ao lado de `auditor/tests.py`. Em Python, o
novo pacote escondia o arquivo com a suíte histórica e o Django informava zero
testes. Os novos diagnósticos IARA foram movidos para `testes/iara/`, sem mudar
o runtime, e a suíte original voltou a executar normalmente.

## Evidências locais

- testes offline de tradução Responses e eventos SSE: aprovados;
- teste HTTP do adaptador com cliente falso, incluindo autenticação e tool call:
  aprovado;
- suíte Django: 49 testes aprovados;
- build Angular: concluído;
- `git diff --check`: aprovado;
- nenhum padrão de credencial real encontrado nos arquivos importados.

Os testes reais contra IARA não foram executados nesta máquina porque o SDK
corporativo e a rede interna não estão disponíveis neste ambiente.

## Limitações atuais

1. `requirements.txt` apenas referencia de forma comentada
   `iara_genai_sdk==0.22.7`. O pacote e a versão devem ser confirmados e
   instalados pelo repositório corporativo aprovado do Itaú.
2. `ATENA_IARA_ENABLED` é uma configuração global do processo. Quando `true`,
   todas as novas execuções desse processo usam IARA; ainda não existe seleção
   independente por conversa na interface.
3. A execução real depende de o gateway Responses/SSE do IARA ser alcançável na
   rede do ambiente.
4. O ID configurado em `IARA_MODEL` precisa existir no catálogo IARA e determina
   o provedor interno, como Bedrock para modelos Claude.
5. Alterar o `.env` exige reiniciar Django e worker para recarregar a
   configuração.

## Validação no ambiente Itaú

Sem imprimir ou compartilhar as credenciais:

```bash
source .venv/bin/activate
python manage.py iara_status
python -m testes.iara.check_iara_responses_reachable
python -m testes.iara.test_iara_adapter
python -m testes.iara.test_iara_adapter_http
```

Depois de confirmar a rede e o modelo, os testes reais podem ser executados de
forma consciente:

```bash
python -m testes.iara.e2e_codex_iara
python -m testes.iara.e2e_codex_iara_tools
```

Esses dois últimos comandos fazem chamadas reais e podem consumir cota.

## Rollback

Defina `ATENA_IARA_ENABLED=false` no `.env` e reinicie Django e worker. O
`codex_app_server.py` remove apenas o `config.toml` que ele próprio gerenciou e
retorna ao caminho OpenAI, preservando configurações manuais e os dados das
conversas.
