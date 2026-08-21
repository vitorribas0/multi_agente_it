import { Injectable, NgZone } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

import {
  ActiveKnowledgeRef,
  ChatDonePayload,
  ChatProgressEvent,
  ConversationDetail,
  ConversationSummary,
  DatasetPage,
  KnowledgeBase,
  SessionAgent,
  SessionAgentDoc,
  SessionAgentSavePayload,
  UploadResult,
} from './chat.models';

// Progresso do upload em lote (evento SSE do /api/upload-batch/).
export interface BatchProgressEvent {
  done?: number;
  total?: number;
  filename?: string;
}

// Payload enviado ao /api/chat/stream/.
export interface ChatStreamRequest {
  message: string;
  conversation_id: number | null;
  agent_slug: string | null;
  engine?: 'codex' | 'legacy';
  active_kbs?: unknown[];
  active_knowledge?: unknown[];
  // Vínculo de playbook. undefined = não mexe; null = desvincula; número = vincula.
  playbook_id?: number | null;
}

// Callbacks do consumo do stream. onProgress recebe cada evento 'progress';
// a Promise resolve com o payload final (evento 'done') ou {status:'error'}.
export interface StreamHandlers {
  onProgress?: (evt: ChatProgressEvent) => void;
  signal?: AbortSignal;
}

@Injectable({ providedIn: 'root' })
export class ChatService {
  constructor(private http: HttpClient, private zone: NgZone) {}

  // ── Histórico ──────────────────────────────────────────────────
  listConversations(): Observable<{ conversations: ConversationSummary[] }> {
    return this.http.get<{ conversations: ConversationSummary[] }>('/api/conversations/');
  }

  getConversation(id: number | string): Observable<ConversationDetail> {
    return this.http.get<ConversationDetail>(`/api/conversations/${id}/`);
  }

  // Renomeia a conversa. O backend faz strip + trunca em 120 chars e devolve
  // o resumo atualizado. Aceita PATCH ou POST; usamos PATCH como o main.js.
  renameConversation(
    id: number | string,
    title: string,
  ): Observable<{ status: string; conversation?: ConversationSummary; message?: string }> {
    return this.http.request<{ status: string; conversation?: ConversationSummary; message?: string }>(
      'PATCH',
      `/api/conversations/${id}/rename/`,
      { body: { title } },
    );
  }

  deleteConversation(id: number | string): Observable<{ status: string }> {
    return this.http.request<{ status: string }>('DELETE', `/api/conversations/${id}/delete/`);
  }

  // Paginação do dataset corrente da conversa (sem passar pela LLM).
  getDataset(convId: number | string, offset: number, limit: number): Observable<DatasetPage> {
    return this.http.get<DatasetPage>(
      `/api/conversations/${convId}/dataset/?offset=${offset}&limit=${limit}`,
    );
  }

  // ── Bases de conhecimento (RAG / IARA) ─────────────────────────
  // Catálogo global vindo da API externa IARA. refresh=1 força atualizar cache.
  listKbs(refresh = false): Observable<{ status: string; kbs: KnowledgeBase[] }> {
    const q = refresh ? '?refresh=1' : '';
    return this.http.get<{ status: string; kbs: KnowledgeBase[] }>(`/api/kbs/${q}`);
  }

  // KBs ativas da conversa (conversation.state["active_kbs"]).
  getConversationKbs(convId: number | string): Observable<{ status: string; active_kbs: KnowledgeBase[] }> {
    return this.http.get<{ status: string; active_kbs: KnowledgeBase[] }>(
      `/api/conversations/${convId}/kbs/`,
    );
  }

  saveConversationKbs(
    convId: number | string,
    activeKbs: KnowledgeBase[],
  ): Observable<{ status: string; active_kbs: KnowledgeBase[]; message?: string }> {
    return this.http.post<{ status: string; active_kbs: KnowledgeBase[]; message?: string }>(
      `/api/conversations/${convId}/kbs/save/`,
      { active_kbs: activeKbs },
    );
  }

  // ── Conhecimentos ativos da conversa ───────────────────────────
  // O catálogo global (/api/knowledge/) é servido pelo ConfigService. Aqui só
  // os ativos da conversa (conversation.state["active_knowledge"] = [{id}]).
  getConversationKnowledge(
    convId: number | string,
  ): Observable<{ status: string; active_knowledge: ActiveKnowledgeRef[] }> {
    return this.http.get<{ status: string; active_knowledge: ActiveKnowledgeRef[] }>(
      `/api/conversations/${convId}/knowledge/`,
    );
  }

  saveConversationKnowledge(
    convId: number | string,
    activeKnowledge: ActiveKnowledgeRef[],
  ): Observable<{ status: string; active_knowledge: ActiveKnowledgeRef[]; message?: string }> {
    return this.http.post<{ status: string; active_knowledge: ActiveKnowledgeRef[]; message?: string }>(
      `/api/conversations/${convId}/knowledge/save/`,
      { active_knowledge: activeKnowledge },
    );
  }

  // ── Agente da sessão ───────────────────────────────────────────
  getSessionAgent(convId: number | string): Observable<{ status: string; agent?: SessionAgent; message?: string }> {
    return this.http.get<{ status: string; agent?: SessionAgent; message?: string }>(
      `/api/conversations/${convId}/session-agent/`,
    );
  }

  saveSessionAgent(
    convId: number | string,
    payload: SessionAgentSavePayload,
  ): Observable<{ status: string; agent?: SessionAgent; message?: string }> {
    return this.http.post<{ status: string; agent?: SessionAgent; message?: string }>(
      `/api/conversations/${convId}/session-agent/save/`,
      payload,
    );
  }

  deleteSessionAgent(convId: number | string): Observable<{ status: string; message?: string }> {
    return this.http.request<{ status: string; message?: string }>(
      'DELETE',
      `/api/conversations/${convId}/session-agent/delete/`,
    );
  }

  // Cria uma conversa nova já com o agente da sessão (quando não há conversa).
  createConversationWithAgent(
    payload: SessionAgentSavePayload,
  ): Observable<{ status: string; conversation_id?: number; agent?: SessionAgent; message?: string }> {
    return this.http.post<{ status: string; conversation_id?: number; agent?: SessionAgent; message?: string }>(
      '/api/session-agent/create-conversation/',
      payload,
    );
  }

  // Extrai texto de um documento (PDF/TXT/MD/DOCX) sem persistir. O objeto
  // retornado (com `markdown`) é reenviado em `documents` no save do agente.
  async extractSessionAgentDocument(file: File): Promise<{ status: string; document?: SessionAgentDoc; message?: string }> {
    const fd = new FormData();
    fd.append('file', file);
    const res = await fetch('/api/session-agent/extract-document/', { method: 'POST', body: fd });
    try {
      return await res.json();
    } catch {
      return { status: 'error', message: 'HTTP ' + res.status };
    }
  }

  // ── Upload ─────────────────────────────────────────────────────
  // Upload único (CSV/XLSX/PDF/DOCX/imagem) — resposta JSON simples.
  async uploadTable(
    file: File,
    opts: { conversationId?: number | null; agentSlug?: string | null; note?: string } = {},
  ): Promise<UploadResult> {
    const fd = new FormData();
    fd.append('file', file);
    if (opts.conversationId) fd.append('conversation_id', String(opts.conversationId));
    if (opts.agentSlug) fd.append('agent_slug', opts.agentSlug);
    if (opts.note) fd.append('note', opts.note);

    const res = await fetch('/api/upload/', { method: 'POST', body: fd });
    const text = await res.text();
    try {
      return JSON.parse(text);
    } catch {
      // Django devolveu HTML (página de debug) — extrai algo útil.
      const snippet = text.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 300);
      return { status: 'error', message: `HTTP ${res.status} (resposta não-JSON): ${snippet || 'sem detalhe'}` };
    }
  }

  // Upload em lote (vários PDFs/TXTs ou pasta) — resposta em SSE com progresso
  // por arquivo. Reaproveita o mesmo parser de frames do chat_stream.
  async uploadBatch(
    files: File[],
    opts: {
      conversationId?: number | null;
      agentSlug?: string | null;
      note?: string;
      onProgress?: (evt: BatchProgressEvent) => void;
    } = {},
  ): Promise<UploadResult> {
    const fd = new FormData();
    files.forEach((f) => fd.append('files', f));
    if (opts.conversationId) fd.append('conversation_id', String(opts.conversationId));
    if (opts.agentSlug) fd.append('agent_slug', opts.agentSlug);
    if (opts.note) fd.append('note', opts.note);

    let response: Response;
    try {
      response = await fetch('/api/upload-batch/', { method: 'POST', body: fd });
    } catch (err: any) {
      return { status: 'error', message: err?.message || 'Falha na conexão' };
    }

    if (!response.ok || !response.body) {
      try {
        return await response.json();
      } catch {
        return { status: 'error', message: 'HTTP ' + response.status };
      }
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let finalPayload: UploadResult | null = null;

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let sep: number;
      while ((sep = buffer.indexOf('\n\n')) !== -1) {
        const frame = buffer.slice(0, sep);
        buffer = buffer.slice(sep + 2);
        const line = frame.split('\n').find((l) => l.startsWith('data:'));
        if (!line) continue;
        let evt: any;
        try {
          evt = JSON.parse(line.slice(5).trim());
        } catch {
          continue;
        }
        if (evt.type === 'progress') {
          if (opts.onProgress) this.zone.run(() => opts.onProgress!(evt));
        } else if (evt.type === 'done') {
          finalPayload = evt.payload;
        } else if (evt.type === 'error') {
          finalPayload = { status: 'error', message: evt.message };
        }
      }
    }
    return finalPayload || { status: 'error', message: 'Stream encerrado sem resposta.' };
  }

  // ── Streaming SSE ──────────────────────────────────────────────
  // Usa fetch + ReadableStream porque o HttpClient do Angular não entrega
  // o corpo em pedaços (só no fim). Isto espelha o consumeStream do chat.js:
  // frames separados por linha em branco, cada um com uma linha 'data: {json}'.
  // Heartbeats (': keep-alive') e linhas sem 'data:' são ignorados.
  async stream(req: ChatStreamRequest, handlers: StreamHandlers = {}): Promise<ChatDonePayload> {
    // O backend usa um sentinela: ausência da chave playbook_id → não mexe no
    // vínculo. Só a incluímos quando o chamador de fato quer definir (número ou
    // null explícito p/ desvincular), evitando resetar o binding a cada envio.
    const body: Record<string, unknown> = {
      message: req.message,
      conversation_id: req.conversation_id,
      agent_slug: req.agent_slug,
      active_kbs: req.active_kbs || [],
      active_knowledge: req.active_knowledge || [],
    };
    if (req.playbook_id !== undefined) body['playbook_id'] = req.playbook_id;

    let response: Response;
    try {
      const endpoint = req.engine === 'codex' ? '/api/codex/chat/stream/' : '/api/chat/stream/';
      response = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        signal: handlers.signal,
      });
    } catch (err: any) {
      if (err?.name === 'AbortError') throw err;
      return { status: 'error', message: err?.message || 'Falha na conexão', conversation_id: 0 };
    }

    if (!response.ok || !response.body) {
      // Fallback: backend pode ter respondido JSON de erro (ex.: 400).
      try {
        return await response.json();
      } catch {
        return { status: 'error', message: 'HTTP ' + response.status, conversation_id: 0 };
      }
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let finalPayload: ChatDonePayload | null = null;

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let sep: number;
      while ((sep = buffer.indexOf('\n\n')) !== -1) {
        const frame = buffer.slice(0, sep);
        buffer = buffer.slice(sep + 2);
        const line = frame.split('\n').find((l) => l.startsWith('data:'));
        if (!line) continue;
        let evt: any;
        try {
          evt = JSON.parse(line.slice(5).trim());
        } catch {
          continue;
        }
        if (evt.type === 'progress') {
          // fetch roda fora da zona do Angular; garante detecção de mudança.
          if (handlers.onProgress) this.zone.run(() => handlers.onProgress!(evt));
        } else if (evt.type === 'done') {
          finalPayload = evt.payload;
        } else if (evt.type === 'error') {
          finalPayload = { status: 'error', message: evt.message, conversation_id: 0 };
        }
      }
    }

    return finalPayload || { status: 'error', message: 'Stream encerrado sem resposta.', conversation_id: 0 };
  }
}
