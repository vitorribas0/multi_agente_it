import { Component, Input } from '@angular/core';
import { CommonModule } from '@angular/common';

import { DocumentAttachment } from '../../api/chat.models';
import { MarkdownDirective } from '../../shared/markdown.directive';

// Card de documento (PDF/DOCX/imagem extraído). Mostra metadados e o conteúdo
// extraído em markdown dentro de um <details>. Espelha o renderDocumentCard.
@Component({
  selector: 'app-document-card',
  standalone: true,
  imports: [CommonModule, MarkdownDirective],
  styles: [`:host { display: block; min-width: 0; max-width: 100%; }`],
  template: `
    <div class="table-card">
      <div class="table-card-header">
        <span class="table-card-icon">📄</span>
        <div class="table-card-meta">
          <div class="table-card-title">{{ att.filename || 'documento' }}</div>
          <div class="table-card-sub">
            {{ (att.char_count || 0).toLocaleString('pt-BR') }} caracteres<span *ngIf="att.page_count"> · {{ att.page_count }} página(s)</span>
          </div>
        </div>
      </div>
      <details class="attachment-summary" style="margin:0">
        <summary>Mostrar conteúdo extraído (markdown)</summary>
        <div class="attachment-summary-body" style="max-height:320px;overflow:auto"
             [appMarkdown]="att.preview || ''"></div>
      </details>
    </div>
  `,
})
export class DocumentCardComponent {
  @Input({ required: true }) att!: DocumentAttachment;
}
