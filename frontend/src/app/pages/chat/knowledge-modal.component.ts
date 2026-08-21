import { Component, EventEmitter, HostListener, Input, OnChanges, Output, SimpleChanges } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';

import { ConfigService } from '../../api/config.service';
import { Knowledge } from '../../api/config.models';
import { ActiveKnowledgeRef } from '../../api/chat.models';

// Modal "Conhecimentos (prompts de especialista)". Espelha openKnowModal do
// chat.js. Diferenças em relação ao KB: catálogo vem do ConfigService
// (/api/knowledge/), cada linha tem ícone e há aviso quando >1 ativo.
// O que sai no (save) é [{id}] — o backend só guarda ids.
@Component({
  selector: 'app-knowledge-modal',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './knowledge-modal.component.html',
})
export class KnowledgeModalComponent implements OnChanges {
  @Input() open = false;
  // Conhecimentos já ativos na conversa (só ids).
  @Input() active: ActiveKnowledgeRef[] = [];

  @Output() save = new EventEmitter<ActiveKnowledgeRef[]>();
  @Output() closed = new EventEmitter<void>();

  readonly KNOWLEDGE_MAX = 10;

  catalog: Knowledge[] = [];
  loading = false;
  search = '';
  draft = new Set<number>();

  constructor(private config: ConfigService) {}

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['open'] && this.open) this.onOpen();
  }

  private onOpen(): void {
    this.draft = new Set(this.active.map((k) => Number(k.id)));
    this.search = '';
    document.body.classList.add('modal-open');
    // Sempre recarrega: os conhecimentos são editados na tela de Configurações.
    this.fetchCatalog();
  }

  private fetchCatalog(): void {
    this.loading = true;
    this.config.listKnowledge().subscribe({
      next: (data) => {
        this.catalog = data.knowledge || [];
        this.loading = false;
      },
      error: () => {
        this.loading = false;
      },
    });
  }

  get filtered(): Knowledge[] {
    const q = this.search.trim().toLowerCase();
    if (!q) return this.catalog;
    return this.catalog.filter(
      (k) => k.name.toLowerCase().includes(q) || (k.description || '').toLowerCase().includes(q),
    );
  }

  get count(): number {
    return this.draft.size;
  }

  get showMultiWarn(): boolean {
    return this.draft.size > 1;
  }

  isSelected(id: number): boolean {
    return this.draft.has(id);
  }

  isDisabled(id: number): boolean {
    return !this.isSelected(id) && this.draft.size >= this.KNOWLEDGE_MAX;
  }

  toggle(k: Knowledge): void {
    if (this.draft.has(k.id)) {
      this.draft.delete(k.id);
    } else {
      if (this.draft.size >= this.KNOWLEDGE_MAX) return;
      this.draft.add(k.id);
    }
  }

  clear(): void {
    this.draft.clear();
  }

  onSave(): void {
    const selected: ActiveKnowledgeRef[] = Array.from(this.draft).map((id) => ({ id }));
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
