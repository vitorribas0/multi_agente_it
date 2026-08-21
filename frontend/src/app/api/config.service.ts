import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

import {
  ConfigOverview,
  AgentSavePayload,
  KnowledgePayload,
  Knowledge,
  WriteResult,
} from './config.models';

// Serviço da tela de Configurações. Fala com os mesmos endpoints /api/*
// que o settings.js consumia — o contrato não mudou. Os POSTs mandam JSON;
// não há header CSRF porque as views são @csrf_exempt no backend.
@Injectable({ providedIn: 'root' })
export class ConfigService {
  constructor(private http: HttpClient) {}

  getConfig(): Observable<ConfigOverview> {
    return this.http.get<ConfigOverview>('/api/config/');
  }

  saveSettings(patch: Partial<{ max_iterations: number; massiva_workers: number }>): Observable<WriteResult> {
    return this.http.post<WriteResult>('/api/config/settings/', patch);
  }

  saveAgent(slug: string, payload: AgentSavePayload): Observable<WriteResult> {
    return this.http.post<WriteResult>(`/api/config/agents/${slug}/`, payload);
  }

  // ── Conhecimentos ──────────────────────────────────────────────
  listKnowledge(): Observable<{ knowledge: Knowledge[] }> {
    return this.http.get<{ knowledge: Knowledge[] }>('/api/knowledge/');
  }

  createKnowledge(payload: KnowledgePayload): Observable<WriteResult<Knowledge>> {
    return this.http.post<WriteResult<Knowledge>>('/api/knowledge/create/', payload);
  }

  updateKnowledge(id: number, payload: KnowledgePayload): Observable<WriteResult<Knowledge>> {
    return this.http.post<WriteResult<Knowledge>>(`/api/knowledge/${id}/update/`, payload);
  }

  deleteKnowledge(id: number): Observable<WriteResult> {
    return this.http.post<WriteResult>(`/api/knowledge/${id}/delete/`, {});
  }
}
