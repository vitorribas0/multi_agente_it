import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

import {
  PlaybookDetail,
  PlaybookRevision,
  PlaybookSavePayload,
  PlaybookSummary,
  PlaybookWriteResult,
  PlaybookValidationResult,
} from './playbook.models';

// Serviço da feature Playbooks. Fala com /api/playbooks/*. Os POSTs mandam
// JSON; as views são @csrf_exempt (mesmo padrão de ConfigService).
@Injectable({ providedIn: 'root' })
export class PlaybookService {
  constructor(private http: HttpClient) {}

  list(publishedOnly = false): Observable<{ status: string; playbooks: PlaybookSummary[] }> {
    const suffix = publishedOnly ? '?published_only=1' : '';
    return this.http.get<{ status: string; playbooks: PlaybookSummary[] }>(`/api/playbooks/${suffix}`);
  }

  get(id: number): Observable<{ status: string; playbook: PlaybookDetail }> {
    return this.http.get<{ status: string; playbook: PlaybookDetail }>(`/api/playbooks/${id}/`);
  }

  create(payload: PlaybookSavePayload): Observable<PlaybookWriteResult> {
    return this.http.post<PlaybookWriteResult>('/api/playbooks/create/', payload);
  }

  update(id: number, payload: PlaybookSavePayload): Observable<PlaybookWriteResult> {
    return this.http.post<PlaybookWriteResult>(`/api/playbooks/${id}/update/`, payload);
  }

  validate(payload: PlaybookSavePayload): Observable<PlaybookValidationResult> {
    return this.http.post<PlaybookValidationResult>('/api/playbooks/validate/', payload);
  }

  revisions(id: number): Observable<{ status: string; revisions: PlaybookRevision[] }> {
    return this.http.get<{ status: string; revisions: PlaybookRevision[] }>(
      `/api/playbooks/${id}/revisions/`,
    );
  }

  restore(id: number, version: number): Observable<PlaybookWriteResult> {
    return this.http.post<PlaybookWriteResult>(
      `/api/playbooks/${id}/revisions/${version}/restore/`,
      {},
    );
  }

  delete(id: number): Observable<{ status: string; message?: string }> {
    return this.http.post<{ status: string; message?: string }>(`/api/playbooks/${id}/delete/`, {});
  }

  // Vincula/desvincula (playbookId=null) um playbook a uma conversa.
  bindConversation(
    convId: number,
    playbookId: number | null,
  ): Observable<{ status: string; message?: string }> {
    return this.http.post<{ status: string; message?: string }>(
      `/api/conversations/${convId}/playbook/save/`,
      { playbook_id: playbookId },
    );
  }
}
