import { Injectable } from '@angular/core';
import { Observable, Subject } from 'rxjs';

// Barramento leve entre o chat-page (dentro do router-outlet) e a sidebar de
// histórico (no shell, fora do outlet). O chat emite `notifyChanged()` depois
// de criar/enviar/renomear/excluir uma conversa; a sidebar assina `changed$`
// para recarregar a lista. Substitui o `window.refreshHistory` global do
// main.js legado por um canal tipado e injetável.
@Injectable({ providedIn: 'root' })
export class ConversationBus {
  private readonly _changed = new Subject<void>();

  // A sidebar assina isto para recarregar /api/conversations/.
  get changed$(): Observable<void> {
    return this._changed.asObservable();
  }

  // Chamado pelo chat quando o histórico pode ter mudado.
  notifyChanged(): void {
    this._changed.next();
  }
}
