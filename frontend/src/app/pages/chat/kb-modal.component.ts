import { Component, EventEmitter, HostListener, Input, OnChanges, Output, SimpleChanges } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';

import { ChatService } from '../../api/chat.service';
import { KnowledgeBase } from '../../api/chat.models';

// Modal "Bases de conhecimento (RAG)". Espelha o openKbModal/renderKbList do
// chat.js: busca por nome/descrição, tabela com checkbox, teto de KB_MAX.
// Não persiste sozinho — devolve a seleção pelo (save); quem decide gravar
// em /api/conversations/<id>/kbs/save/ é o chat-page (só quando há conversa).
@Component({
  selector: 'app-kb-modal',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './kb-modal.component.html',
})
export class KbModalComponent implements OnChanges {
  @Input() open = false;
  // KBs já ativas na conversa (para pré-marcar).
  @Input() active: KnowledgeBase[] = [];

  @Output() save = new EventEmitter<KnowledgeBase[]>();
  @Output() closed = new EventEmitter<void>();

  readonly KB_MAX = 10;

  catalog: KnowledgeBase[] = [];
  loading = false;
  loaded = false;
  search = '';
  // ids selecionados (rascunho até salvar).
  draft = new Set<string>();

  constructor(private chat: ChatService) {}

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['open'] && this.open) this.onOpen();
  }

  private onOpen(): void {
    this.draft = new Set(this.active.map((k) => String(k.id)));
    this.search = '';
    document.body.classList.add('modal-open');
    if (!this.loaded) this.fetchCatalog();
  }

  private fetchCatalog(): void {
    this.loading = true;
    this.chat.listKbs().subscribe({
      next: (data) => {
        this.catalog = data.kbs || [];
        this.loaded = true;
        this.loading = false;
      },
      error: () => {
        this.loading = false;
      },
    });
  }

  get filtered(): KnowledgeBase[] {
    const q = this.search.trim().toLowerCase();
    if (!q) return this.catalog;
    return this.catalog.filter(
      (k) => k.name.toLowerCase().includes(q) || (k.description || '').toLowerCase().includes(q),
    );
  }

  get count(): number {
    return this.draft.size;
  }

  isSelected(id: string | number): boolean {
    return this.draft.has(String(id));
  }

  // No teto, os não-marcados ficam desabilitados (igual ao chat.js).
  isDisabled(id: string | number): boolean {
    return !this.isSelected(id) && this.draft.size >= this.KB_MAX;
  }

  toggle(kb: KnowledgeBase): void {
    const id = String(kb.id);
    if (this.draft.has(id)) {
      this.draft.delete(id);
    } else {
      if (this.draft.size >= this.KB_MAX) return;
      this.draft.add(id);
    }
  }

  clear(): void {
    this.draft.clear();
  }

  onSave(): void {
    const selected = this.catalog.filter((k) => this.draft.has(String(k.id)));
    this.save.emit(selected);
    this.close();
  }

  close(): void {
    document.body.classList.remove('modal-open');
    this.closed.emit();
  }

  @HostListener('document:keydown.escape')
  onEsc(): void {
    if (this.open) this.close();
  }
}
