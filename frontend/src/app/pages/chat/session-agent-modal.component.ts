import { Component, ElementRef, EventEmitter, HostListener, Input, OnChanges, Output, SimpleChanges, ViewChild } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';

import { ChatService } from '../../api/chat.service';
import { ConfigService } from '../../api/config.service';
import { ToolInfo } from '../../api/config.models';
import { SessionAgent, SessionAgentDoc, SessionAgentSavePayload } from '../../api/chat.models';

// Documento em edição no modal: além dos campos do backend, guarda um flag
// _loading enquanto a extração está em curso.
interface DocDraft extends SessionAgentDoc {
  _loading?: boolean;
}

// Modal "Agente desta sessão". Espelha openSaModal e cia. do chat.js: form com
// ícone/nome/prompt/guardrails/modelo/temperatura/tools/documentos, extração de
// documentos, importar/exportar .json. Salva via /session-agent/save/ quando há
// conversa ou /session-agent/create-conversation/ quando é chat novo.
@Component({
  selector: 'app-session-agent-modal',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './session-agent-modal.component.html',
})
export class SessionAgentModalComponent implements OnChanges {
  @Input() open = false;
  @Input() conversationId: number | null = null;
  // Já existe agente na conversa? Controla carregar p/ edição e o botão excluir.
  @Input() hasAgent = false;

  // Emite {conversationId, agent} ao salvar (o create-conversation pode gerar id).
  @Output() saved = new EventEmitter<{ conversationId: number | null; agent: SessionAgent }>();
  @Output() deleted = new EventEmitter<void>();
  @Output() closed = new EventEmitter<void>();

  @ViewChild('docInput') docInput?: ElementRef<HTMLInputElement>;
  @ViewChild('importInput') importInput?: ElementRef<HTMLInputElement>;

  models: string[] = [];
  tools: ToolInfo[] = [];
  private configLoaded = false;

  // Estado do formulário.
  icon = '🤖';
  name = '';
  systemPrompt = '';
  guardrails = '';
  model = '';
  temperature = 0.7;
  toolsEnabled = new Set<string>();
  docs: DocDraft[] = [];

  saving = false;

  constructor(private chat: ChatService, private config: ConfigService) {}

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['open'] && this.open) this.onOpen();
  }

  private async onOpen(): Promise<void> {
    document.body.classList.add('modal-open');
    await this.ensureConfig();
    if (this.conversationId && this.hasAgent) {
      this.chat.getSessionAgent(this.conversationId).subscribe({
        next: (data) => {
          if (data.status === 'success' && data.agent) this.fillForm(data.agent);
          else this.resetForm();
        },
        error: () => this.resetForm(),
      });
    } else {
      this.resetForm();
    }
  }

  private ensureConfig(): Promise<void> {
    if (this.configLoaded) return Promise.resolve();
    return new Promise((resolve) => {
      this.config.getConfig().subscribe({
        next: (cfg) => {
          this.models = cfg.models || [];
          this.tools = cfg.tools || [];
          this.configLoaded = true;
          resolve();
        },
        error: () => resolve(),
      });
    });
  }

  private fillForm(a: Partial<SessionAgent>): void {
    this.name = a.name || '';
    this.icon = a.icon || '🤖';
    this.systemPrompt = a.system_prompt || '';
    this.guardrails = a.guardrails || '';
    this.model = a.model && this.models.includes(a.model) ? a.model : this.models[0] || '';
    this.temperature = a.temperature != null ? a.temperature : 0.7;
    this.toolsEnabled = new Set(a.tools_enabled || []);
    this.docs = Array.isArray(a.documents) ? a.documents.map((d) => ({ ...d })) : [];
  }

  private resetForm(): void {
    this.fillForm({ name: '', icon: '🤖', system_prompt: '', guardrails: '', temperature: 0.7, tools_enabled: [], documents: [] });
  }

  get canDelete(): boolean {
    return !!(this.conversationId && this.hasAgent);
  }

  // O input[type=range] com ngModel devolve string; normaliza p/ exibir.
  get tempLabel(): string {
    return Number(this.temperature).toFixed(2);
  }

  get toolsCount(): string {
    const n = this.toolsEnabled.size;
    return n ? `· ${n} selecionada${n === 1 ? '' : 's'}` : '';
  }

  get docsCount(): string {
    const n = this.docs.length;
    return n ? `· ${n} documento${n === 1 ? '' : 's'}` : '';
  }

  isToolChecked(slug: string): boolean {
    return this.toolsEnabled.has(slug);
  }

  toggleTool(slug: string): void {
    if (this.toolsEnabled.has(slug)) this.toolsEnabled.delete(slug);
    else this.toolsEnabled.add(slug);
  }

  fmtCharCount(n?: number | null): string {
    if (n == null) return '';
    if (n >= 1000) return `${(n / 1000).toFixed(1)}k chars`;
    return `${n} chars`;
  }

  docMeta(d: DocDraft): string {
    if (d._loading) return 'extraindo…';
    const pages = d.page_count ? ` · ${d.page_count} pág.` : '';
    return `${this.fmtCharCount(d.char_count)}${pages}`;
  }

  // ── Documentos ─────────────────────────────────────────────────
  pickDocs(): void {
    this.docInput?.nativeElement.click();
  }

  onDocFiles(ev: Event): void {
    const input = ev.target as HTMLInputElement;
    const files = Array.from(input.files || []);
    input.value = '';
    files.forEach((f) => this.uploadDoc(f));
  }

  private async uploadDoc(file: File): Promise<void> {
    const placeholder: DocDraft = { filename: file.name, char_count: null as unknown as number, _loading: true };
    this.docs.push(placeholder);
    try {
      const data = await this.chat.extractSessionAgentDocument(file);
      const idx = this.docs.indexOf(placeholder);
      if (data.status === 'success' && data.document) {
        if (idx !== -1) this.docs[idx] = data.document;
      } else {
        if (idx !== -1) this.docs.splice(idx, 1);
        alert('Não foi possível anexar "' + file.name + '": ' + (data.message || 'erro desconhecido'));
      }
    } catch (e: any) {
      const idx = this.docs.indexOf(placeholder);
      if (idx !== -1) this.docs.splice(idx, 1);
      alert('Falha ao anexar "' + file.name + '": ' + (e?.message || e));
    }
  }

  removeDoc(i: number): void {
    this.docs.splice(i, 1);
  }

  // ── Payload / salvar ───────────────────────────────────────────
  private getFormData(): SessionAgentSavePayload {
    // Docs com markdown (recém-anexados) vão completos; salvos vão só filename.
    const documents: SessionAgentDoc[] = this.docs
      .filter((d) => !d._loading)
      .map((d) =>
        d.markdown != null
          ? { filename: d.filename, markdown: d.markdown, page_count: d.page_count ?? null }
          : { filename: d.filename },
      );
    return {
      name: this.name.trim() || 'Meu agente',
      icon: this.icon.trim() || '🤖',
      system_prompt: this.systemPrompt,
      guardrails: this.guardrails,
      model: this.model,
      temperature: Number(this.temperature),
      tools_enabled: Array.from(this.toolsEnabled),
      documents,
    };
  }

  onSave(): void {
    const payload = this.getFormData();
    this.saving = true;
    const done = (convId: number | null, agent?: SessionAgent) => {
      this.saving = false;
      this.saved.emit({ conversationId: convId, agent: agent || (payload as unknown as SessionAgent) });
      this.close();
    };
    const fail = (msg: string) => {
      this.saving = false;
      alert('Erro ao salvar: ' + msg);
    };

    if (this.conversationId) {
      this.chat.saveSessionAgent(this.conversationId, payload).subscribe({
        next: (data) => (data.status === 'success' ? done(this.conversationId, data.agent) : fail(data.message || 'desconhecido')),
        error: (e) => fail(e?.message || 'falha na conexão'),
      });
    } else {
      this.chat.createConversationWithAgent(payload).subscribe({
        next: (data) =>
          data.status === 'success' && data.conversation_id
            ? done(data.conversation_id, data.agent)
            : fail(data.message || 'desconhecido'),
        error: (e) => fail(e?.message || 'falha na conexão'),
      });
    }
  }

  onDelete(): void {
    if (!this.conversationId || !this.hasAgent) {
      this.close();
      return;
    }
    if (!confirm('Excluir o agente desta sessão? Isso não afeta os agentes do sistema.')) return;
    this.chat.deleteSessionAgent(this.conversationId).subscribe({
      next: () => {
        this.deleted.emit();
        this.close();
      },
      error: (e) => alert('Falha ao excluir: ' + (e?.message || e)),
    });
  }

  // ── Import / export ────────────────────────────────────────────
  onExport(): void {
    const data = this.getFormData();
    const bundle = { _kind: 'tech-auditor.session-agent', _version: 1, ...data };
    const blob = new Blob([JSON.stringify(bundle, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    const stem =
      (data.name || 'agente')
        .toLowerCase()
        .normalize('NFD')
        .replace(/[̀-ͯ]/g, '')
        .replace(/[^a-z0-9]+/g, '_')
        .replace(/^_+|_+$/g, '')
        .slice(0, 40) || 'agente';
    a.href = url;
    a.download = `agente_${stem}.json`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  pickImport(): void {
    this.importInput?.nativeElement.click();
  }

  onImportFile(ev: Event): void {
    const input = ev.target as HTMLInputElement;
    const file = (input.files || [])[0];
    input.value = '';
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (e) => {
      try {
        const data = JSON.parse(String(e.target?.result));
        if (data._kind && data._kind !== 'tech-auditor.session-agent') {
          if (!confirm('Este arquivo não parece ser um agente do Vitor_companhia. Importar mesmo assim?')) return;
        }
        this.fillForm(data);
      } catch (err: any) {
        alert('Arquivo inválido: ' + (err?.message || err));
      }
    };
    reader.readAsText(file);
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
