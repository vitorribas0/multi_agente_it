import {
  Component,
  ElementRef,
  HostListener,
  Input,
  OnDestroy,
  OnInit,
  ViewChild,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { DomSanitizer, SafeHtml } from '@angular/platform-browser';

import { ExportAttachment } from '../../api/chat.models';

// Card de export (CSV/XLSX/PDF/HTML baixável). Espelha o renderExportCard do chat.js.
// Para HTML, além de baixar, oferece "Visualizar": abre a página renderizada
// num overlay isolado (iframe sandbox, sem same-origin) — os gráficos rodam
// mas o código não acessa o app.
@Component({
  selector: 'app-export-card',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './export-card.component.html',
  styles: [`:host { display: block; min-width: 0; max-width: 100%; }`],
})
export class ExportCardComponent implements OnInit, OnDestroy {
  @Input({ required: true }) att!: ExportAttachment;

  // Painel movido p/ o <body> ao abrir: escapa de ancestrais com `transform`
  // (a .message tem animation com transform, que quebraria o position:fixed).
  @ViewChild('previewPanel') previewPanelRef?: ElementRef<HTMLElement>;
  private movedPanel: HTMLElement | null = null;

  fmt = '';
  fileIcon = '📄';
  iconClass = 'csv';
  detailTag = '';
  sizeStr = '';

  // Preview (só HTML)
  expanded = false;
  fullscreen = false;
  loadingPreview = false;
  previewError = '';
  srcdoc: SafeHtml | null = null;

  constructor(private sanitizer: DomSanitizer) {}

  ngOnInit(): void {
    this.fmt = (this.att.formato || '').toLowerCase();
    const sizeKb = this.att.size_kb ?? null;
    this.sizeStr = sizeKb == null ? '' : sizeKb >= 1024 ? `${(sizeKb / 1024).toFixed(1)} MB` : `${sizeKb} KB`;

    if (this.fmt === 'pdf') {
      const pgs = this.att.paginas ?? null;
      this.detailTag = pgs != null ? `${pgs} página${pgs === 1 ? '' : 's'}` : 'Documento PDF';
      this.fileIcon = '📕';
      this.iconClass = 'pdf';
    } else if (this.fmt === 'html') {
      this.detailTag = 'Página HTML';
      this.fileIcon = '🌐';
      this.iconClass = 'html';
    } else {
      const linhas = this.att.linhas != null ? this.att.linhas.toLocaleString('pt-BR') : '?';
      const colunas = this.att.colunas != null ? this.att.colunas : '?';
      this.detailTag = `${linhas} linhas · ${colunas} colunas`;
      this.fileIcon = this.fmt === 'xlsx' ? '📊' : '📄';
      this.iconClass = this.fmt === 'xlsx' ? 'xlsx' : 'csv';
    }
  }

  get filename(): string {
    return this.att.filename || 'export';
  }

  get canPreview(): boolean {
    return this.fmt === 'html' && !!this.att.download_url;
  }

  get title(): string {
    return this.att.titulo || this.filename;
  }

  // ── Preview em overlay isolado ─────────────────────────────────
  async openPreview(): Promise<void> {
    if (!this.canPreview) return;
    this.expanded = true;
    document.body.classList.add('html-preview-open');
    // Espera o *ngIf criar o painel e então move-o p/ o <body>.
    setTimeout(() => this.movePanelToBody());

    // Carrega o HTML uma vez e injeta via srcdoc num iframe sandbox.
    if (this.srcdoc === null && !this.loadingPreview) {
      this.loadingPreview = true;
      this.previewError = '';
      try {
        const resp = await fetch(this.att.download_url!);
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const html = await resp.text();
        // bypassSecurityTrustHtml só alimenta o [srcdoc]; o isolamento real
        // vem do sandbox do iframe (allow-scripts SEM allow-same-origin).
        this.srcdoc = this.sanitizer.bypassSecurityTrustHtml(html);
      } catch (e: any) {
        this.previewError = 'Não consegui carregar a apresentação: ' + (e?.message || e);
      } finally {
        this.loadingPreview = false;
      }
    }
  }

  private movePanelToBody(): void {
    const el = this.previewPanelRef?.nativeElement;
    if (el && el.parentElement !== document.body) {
      document.body.appendChild(el);
      this.movedPanel = el;
    }
  }

  private cleanupPanel(): void {
    if (this.movedPanel && this.movedPanel.parentElement === document.body) {
      this.movedPanel.remove();
    }
    this.movedPanel = null;
  }

  closePreview(): void {
    this.expanded = false;
    this.fullscreen = false;
    document.body.classList.remove('html-preview-open', 'html-preview-full');
    this.cleanupPanel();
  }

  ngOnDestroy(): void {
    document.body.classList.remove('html-preview-open', 'html-preview-full');
    this.cleanupPanel();
  }

  toggleFullscreen(): void {
    this.fullscreen = !this.fullscreen;
    // Em tela cheia o painel cobre tudo — o chat não precisa reservar espaço.
    document.body.classList.toggle('html-preview-full', this.fullscreen);
  }

  @HostListener('document:keydown.escape')
  onEsc(): void {
    if (this.fullscreen) this.toggleFullscreen();
    else if (this.expanded) this.closePreview();
  }
}
