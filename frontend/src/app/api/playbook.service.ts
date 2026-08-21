import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

import {
  PlaybookDetail,
  PlaybookSavePayload,
  PlaybookSummary,
  PlaybookWriteResult,
} from './playbook.models';

// Serviço da feature Playbooks. Fala com /api/playbooks/*. Os POSTs mandam
// JSON; as views são @csrf_exempt (mesmo padrão de ConfigService).
@Injectable({ providedIn: 'root' })
export class PlaybookService {
  constructor(private http: HttpClient) {}

  list(): Observable<{ status: string; playbooks: PlaybookSummary[] }> {
    return this.http.get<{ status: string; playbooks: PlaybookSummary[] }>('/api/playbooks/');
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
