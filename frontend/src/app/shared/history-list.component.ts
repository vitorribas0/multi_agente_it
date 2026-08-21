import { Component, OnDestroy, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { NavigationEnd, Router } from '@angular/router';
import { Subscription, filter } from 'rxjs';

import { ChatService } from '../api/chat.service';
import { ConversationBus } from '../api/conversation-bus.service';
import { ConversationSummary } from '../api/chat.models';
import { AutofocusDirective } from './autofocus.directive';

// Sidebar de histórico de conversas. Espelha o renderHistory/startRename/
// deleteConversation do main.js legado, mas em SPA: navega por
// Router.navigate(?c=<id>) em vez de recarregar a página, e recarrega a lista
// ao receber `changed$` do ConversationBus (substitui window.refreshHistory).
@Component({
  selector: 'app-history-list',
  standalone: true,
  imports: [CommonModule, FormsModule, AutofocusDirective],
  templateUrl: './history-list.component.html',
})
export class HistoryListComponent implements OnInit, OnDestroy {
  conversations: ConversationSummary[] = [];
  activeId: number | null = null;

  // Conversa em renomeação (id) e o texto do input inline.
  renamingId: number | null = null;
  renameText = '';

  private subs = new Subscription();

  constructor(private chat: ChatService, private router: Router, private bus: ConversationBus) {}

  ngOnInit(): void {
    this.readActiveId();
    this.load();
    // Recarrega quando o chat cria/envia/muda uma conversa.
    this.subs.add(this.bus.changed$.subscribe(() => this.load()));
    // Mantém o item ativo em sincronia com a URL (?c=<id>).
    this.subs.add(
      this.router.events
        .pipe(filter((e) => e instanceof NavigationEnd))
        .subscribe(() => this.readActiveId()),
    );
  }

  ngOnDestroy(): void {
    this.subs.unsubscribe();
  }

  private readActiveId(): void {
    const c = this.router.parseUrl(this.router.url).queryParamMap.get('c');
    this.activeId = c ? Number(c) : null;
  }

  private load(): void {
    this.chat.listConversations().subscribe({
      next: (data) => (this.conversations = data.conversations || []),
      error: () => {},
    });
  }

  isActive(conv: ConversationSummary): boolean {
    return this.activeId === conv.id;
  }

  // ── Navegação ──────────────────────────────────────────────────
  newChat(): void {
    // Sem ?c: o chat-page limpa o estado (nova conversa).
    this.router.navigate(['/chat']);
  }

  open(conv: ConversationSummary): void {
    if (this.renamingId === conv.id) return;
    this.router.navigate(['/chat'], { queryParams: { c: conv.id } });
  }

  // ── Renomear ───────────────────────────────────────────────────
  startRename(conv: ConversationSummary, ev: Event): void {
    ev.preventDefault();
    ev.stopPropagation();
    this.renamingId = conv.id;
    this.renameText = conv.title || '';
  }

  commitRename(conv: ConversationSummary): void {
    if (this.renamingId !== conv.id) return;
    const newTitle = this.renameText.trim();
    this.renamingId = null;
    if (newTitle && newTitle !== conv.title) {
      this.chat.renameConversation(conv.id, newTitle).subscribe({
        next: () => this.load(),
        error: () => {},
      });
    }
  }

  cancelRename(): void {
    this.renamingId = null;
  }

  onRenameKeydown(ev: KeyboardEvent, conv: ConversationSummary): void {
    if (ev.key === 'Enter') {
      ev.preventDefault();
      this.commitRename(conv);
    } else if (ev.key === 'Escape') {
      ev.preventDefault();
      this.cancelRename();
    }
  }

  // ── Excluir ────────────────────────────────────────────────────
  // Sem confirmação, como o main.js. Se apagar a conversa aberta, vai p/ nova.
  remove(conv: ConversationSummary, ev: Event): void {
    ev.preventDefault();
    ev.stopPropagation();
    this.chat.deleteConversation(conv.id).subscribe({
      next: () => {
        if (this.activeId === conv.id) this.router.navigate(['/chat']);
        this.load();
      },
      error: () => {},
    });
  }

  trackConv = (_: number, c: ConversationSummary) => c.id;
}
