# Frontend Angular — Multi-Agentes Auditoria

Prova de conceito da migração do frontend (hoje Django templates + JS) para
**Angular**, mantendo o **backend Python/Django intacto** como API.

Nesta primeira fase só a tela de **Configurações** (`/settings`) foi portada —
é a mais simples e exercita GET/POST/DELETE contra os endpoints reais
(`/api/config/`, `/api/config/settings/`, `/api/config/agents/<slug>/`,
`/api/knowledge/*`). O objetivo é validar ponta a ponta: build, serve, proxy,
consumo de API e paridade visual (reusa o `style.css` original).

## Pré-requisitos

- **Node.js 18.19+ ou 20.9+** (exigência do Angular 17). Confira com `node -v`.
- O **backend Django rodando** em `http://localhost:8000` (ver `../restart.sh`).

## Como rodar (em dev)

Em **dois terminais**:

**1) Backend (Django)** — na raiz do projeto:
```bash
./restart.sh          # sobe o Django em 0.0.0.0:8000
```

**2) Frontend (Angular)** — dentro de `frontend/`:
```bash
cd frontend
npm install           # só na primeira vez (baixa as dependências)
npm start             # ng serve com proxy -> http://localhost:4200
```

Abra **http://localhost:4200** — cai direto em `/settings`.

### Por que não precisa configurar CORS

O `npm start` usa `ng serve --proxy-config proxy.conf.json`, que encaminha
tudo que começa com `/api` para `http://localhost:8000`. Para o navegador, front
e API estão na mesma origem (`localhost:4200`), então **não há CORS** nem
cookies cross-site. Isso espelha o cenário de produção (CloudFront serve o
estático e o API Gateway expõe a API sob o mesmo domínio).

> Em produção o alvo do proxy vira a URL do API Gateway; em dev aponta para o
> Django local. Nenhuma mudança de código, só de configuração.

## Build de produção

```bash
npm run build         # gera dist/auditoria-frontend/ (estático p/ S3+CloudFront)
```

## Estrutura

```
frontend/
├── proxy.conf.json                 # /api -> Django (dev)
├── angular.json                    # build/serve (Angular 17, standalone)
├── src/
│   ├── index.html                  # shell HTML + fonte Inter
│   ├── styles.css                  # CSS reaproveitado do projeto Django
│   ├── main.ts                     # bootstrap (HttpClient + Router)
│   └── app/
│       ├── app.component.*         # shell: sidebar + <router-outlet>
│       ├── app.routes.ts           # rotas (só /settings por ora)
│       ├── api/
│       │   ├── config.models.ts    # tipos do contrato JSON
│       │   └── config.service.ts   # chamadas HTTP a /api/config e /api/knowledge
│       └── pages/settings/
│           ├── settings-page.component.ts
│           └── settings-page.component.html
```

## Estado da migração

- [x] Fundação Angular + shell (sidebar) + proxy
- [x] Tela de **Configurações** (Geral, Agentes, Conhecimentos, Tools)
- [x] Tela de **Chat — núcleo**: enviar mensagem, resposta em **streaming SSE**
      (`/api/chat/stream/` via `fetch`+ReadableStream), histórico de conversa
      (`/api/conversations/<id>/`), markdown (marked + highlight.js) e log de
      progresso ao vivo (balão "pensando")
- [ ] Tela de **Chat — completo**: upload de tabelas/arquivos, cards de tabela,
      gráficos, fluxograma (Mermaid), árvore de tool-calls, modais de agente da
      sessão / KBs / conhecimentos
- [ ] Tela de **Manual**
- [ ] Histórico de conversas na sidebar
