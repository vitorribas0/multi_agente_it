import { Component, HostListener, Input, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';

import { ChatService } from '../../api/chat.service';
import { TableAttachment } from '../../api/chat.models';

// Card de tabela: preview de 5 linhas por padrão; ao expandir vira um overlay
// em tela cheia com paginação servida por /api/conversations/<id>/dataset/.
// Espelha o renderTableCard do chat.js (mesmas classes CSS).
@Component({
  selector: 'app-table-card',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './table-card.component.html',
  // O host do componente é inline por padrão; sem virar bloco com min-width:0
  // ele não fica preso à largura do .msg-content-wrap e uma tabela com muitas
  // colunas "estoura" pro lado em vez de acionar o scroll horizontal interno.
  styles: [`:host { display: block; min-width: 0; max-width: 100%; }`],
})
export class TableCardComponent implements OnInit {
  @Input({ required: true }) att!: TableAttachment;
  // Necessário para paginar o dataset corrente da conversa.
  @Input() conversationId: number | null = null;

  expanded = false;
  offset = 0;
  limit = 100;
  rows: Array<Record<string, unknown>> = [];
  total = 0;

  private previewRows: Array<Record<string, unknown>> = [];

  constructor(private chat: ChatService) {}

  ngOnInit(): void {
    this.previewRows = this.att.preview || [];
    this.rows = this.previewRows.slice();
    this.total = this.att.rows || 0;
  }

  get columns(): string[] {
    return this.att.columns || [];
  }

  get dtypes(): Record<string, string> {
    return this.att.dtypes || {};
  }

  // No modo mini mostramos só as 5 primeiras; expandido, a página inteira.
  get visibleRows(): Array<Record<string, unknown>> {
    return this.expanded ? this.rows : this.rows.slice(0, 5);
  }

  get pagerInfo(): string {
    if (this.expanded) {
      const start = this.offset + 1;
      const end = this.offset + this.rows.length;
      return `${start.toLocaleString('pt-BR')}–${end.toLocaleString('pt-BR')} de ${this.total.toLocaleString('pt-BR')}`;
    }
    return `Pré-visualizando 5 de ${this.total.toLocaleString('pt-BR')} linhas`;
  }

  get prevDisabled(): boolean {
    return !this.expanded || this.offset <= 0;
  }

  get nextDisabled(): boolean {
    return !this.expanded || this.offset + this.limit >= this.total;
  }

  cell(row: Record<string, unknown>, col: string): unknown {
    return row[col];
  }

  isNull(v: unknown): boolean {
    return v === null || v === undefined;
  }

  toggle(): void {
    if (this.expanded) this.minimize();
    else this.expand();
  }

  private expand(): void {
    this.expanded = true;
    document.body.classList.add('modal-open');
    this.fetchPage(0);
  }

  minimize(): void {
    if (!this.expanded) return;
    this.expanded = false;
    document.body.classList.remove('modal-open');
    this.rows = this.previewRows.slice();
    this.offset = 0;
  }

  @HostListener('document:keydown.escape')
  onEsc(): void {
    this.minimize();
  }

  prev(): void {
    if (this.prevDisabled) return;
    this.fetchPage(Math.max(0, this.offset - this.limit));
  }

  next(): void {
    if (this.nextDisabled) return;
    this.fetchPage(this.offset + this.limit);
  }

  private fetchPage(offset: number): void {
    if (!this.conversationId) return;
    this.chat.getDataset(this.conversationId, offset, this.limit).subscribe({
      next: (data) => {
        this.rows = data.rows || [];
        this.offset = data.offset || 0;
        this.total = data.total || this.total;
      },
      error: () => {},
    });
  }
}
