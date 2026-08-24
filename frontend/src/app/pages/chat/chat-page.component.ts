import {
  AfterViewChecked,
  Component,
  ElementRef,
  HostListener,
  OnDestroy,
  OnInit,
  ViewChild,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router } from '@angular/router';
import { Subscription } from 'rxjs';

import { ChatService, CodexSkillPrompt } from '../../api/chat.service';
import { ConfigService } from '../../api/config.service';
import { ConversationBus } from '../../api/conversation-bus.service';
import { PlaybookService } from '../../api/playbook.service';
import { PlaybookSuggestion, PlaybookSummary } from '../../api/playbook.models';
import { AgentInfo } from '../../api/config.models';
import {
  ActiveKnowledgeRef,
  Attachment,
  ChartAttachment,
  ChatMessage,
  CodexInteraction,
  CodexPlanEvent,
  CodexPlanItem,
  DocumentAttachment,
  ExecutionEvent,
  ExecutionSnapshot,
  ExportAttachment,
  ChatProgressEvent,
  KnowledgeBase,
  LiveNode,
  MermaidAttachment,
  ProgressLine,
  SessionAgent,
  TableAttachment,
  UploadResult,
} from '../../api/chat.models';
import { MarkdownDirective } from '../../shared/markdown.directive';
import { TableCardComponent } from './table-card.component';
import { DocumentCardComponent } from './document-card.component';
import { ToolCallComponent } from './tool-call.component';
import { LiveNodeComponent } from './live-node.component';
import { ExportCardComponent } from './export-card.component';
import { ChartCardComponent } from './chart-card.component';
import { MermaidCardComponent } from './mermaid-card.component';
import { KbModalComponent } from './kb-modal.component';
import { KnowledgeModalComponent } from './knowledge-modal.component';
import { SessionAgentModalComponent } from './session-agent-modal.component';
import { PlaybookPickerComponent } from './playbook-picker.component';

interface AtenaSkill {
  slug: string;
  icon: string;
  name: string;
  description: string;
  content: string;
}

@Component({
  selector: 'app-chat-page',
  standalone: true,
  imports: [
    CommonModule,
    FormsModule,
    MarkdownDirective,
    TableCardComponent,
    DocumentCardComponent,
    ToolCallComponent,
    LiveNodeComponent,
    ExportCardComponent,
    ChartCardComponent,
    MermaidCardComponent,
    KbModalComponent,
    KnowledgeModalComponent,
    SessionAgentModalComponent,
    PlaybookPickerComponent,
  ],
  templateUrl: './chat-page.component.html',
  // Mesmo motivo da tela de Configurações: o elemento-host fica entre .main e
  // o container rolável; sem participar da cadeia flex, o scroll interno das
  // mensagens não funciona. Ver [[angular-migration]].
  styles: [`
    :host {
      flex: 1;
      display: flex;
      flex-direction: column;
      min-height: 0;
    }
    .engine-switch {
      display: inline-flex;
      padding: 3px;
      border: 1px solid var(--border);
      border-radius: 999px;
      background: var(--bg-elevated);
    }
    .engine-switch button {
      border: 0;
      border-radius: 999px;
      padding: 5px 10px;
      color: var(--text-secondary);
      background: transparent;
      font: inherit;
      font-size: 11px;
      font-weight: 650;
      cursor: pointer;
    }
    .engine-switch button.active {
      color: #fff;
      background: linear-gradient(135deg, #EC7000, #ff9d00);
      box-shadow: 0 3px 12px rgba(236, 112, 0, .24);
    }
    .codex-dot { background: #22c55e !important; box-shadow: 0 0 9px rgba(34,197,94,.7) !important; }
  `],
})
export class ChatPageComponent implements OnInit, OnDestroy, AfterViewChecked {
  @ViewChild('messagesArea') messagesArea?: ElementRef<HTMLElement>;
  @ViewChild('fileInput') fileInput?: ElementRef<HTMLInputElement>;
  @ViewChild('batchFilesInput') batchFilesInput?: ElementRef<HTMLInputElement>;
  @ViewChild('batchFolderInput') batchFolderInput?: ElementRef<HTMLInputElement>;

  // Limite de arquivos por lote (espelha BATCH_MAX_FILES do chat.js).
  private readonly BATCH_MAX_FILES = 200;

  messages: ChatMessage[] = [];
  agents: AgentInfo[] = [];
  selectedAgentSlug: string | null = null;
  conversationId: number | null = null;
  engine: 'codex' | 'legacy' = 'codex';

  input = '';
  isLoading = false;
  awaitingHuman = false;
  pendingInteraction: CodexInteraction | null = null;
  interactionAnswers: Record<string, string> = {};
  interactionSubmitting = false;
  interactionError = '';
  livePlan: CodexPlanItem[] = [];
  livePlanExplanation = '';
  stopRequested = false;

  // Menu de anexos aberto?
  attachMenuOpen = false;
  // Bandeja de arquivos da conversa, no canto superior direito.
  filesMenuOpen = false;
  // Skills disponíveis no ambiente isolado da Atena.
  skillsMenuOpen = false;
  expandedSkill: string | null = null;
  atenaSkills: AtenaSkill[] = [
    { slug: 'auditoria-interna', icon: '🛡️', name: 'Auditoria interna', description: 'Planejamento, testes, riscos, achados e recomendações.', content: '' },
    { slug: 'aws-athena', icon: '☁️', name: 'AWS Athena', description: 'Consultas seguras, catálogo Glue e rastreabilidade de dados.', content: '' },
    { slug: 'ciencia-dados', icon: '📈', name: 'Ciência de dados', description: 'Análise tabular, visualizações, segmentação e anomalias.', content: '' },
    { slug: 'analise-documentos', icon: '📄', name: 'Análise de documentos', description: 'Leitura de PDFs, DOCX e imagens com evidências.', content: '' },
    { slug: 'documentacao-auditoria', icon: '✦', name: 'Documentação de auditoria', description: 'Relatórios, HTML, PDF e indicadores com identidade Itaú.', content: '' },
  ];
  // Placeholder de upload em andamento (texto do que está carregando).
  uploading: string | null = null;

  // Balão "pensando" enquanto o agente processa.
  typing = false;
  progress: ProgressLine[] = [];

  // ── Execução ao vivo (painel lateral estilo artifacts) ────────────
  // Árvore de nós montada incrementalmente a partir dos eventos de progresso.
  liveTree: LiveNode[] = [];
  // Índice id→nó para achar/atualizar em O(1) conforme os eventos chegam.
  private liveIndex = new Map<string, LiveNode>();
  livePanelOpen = false;
  livePanelFull = false;
  // Painel movido p/ <body> ao abrir (escapa de ancestrais com transform).
  @ViewChild('livePanel') livePanelRef?: ElementRef<HTMLElement>;
  private liveMovedPanel: HTMLElement | null = null;

  // ── Modais (agente da sessão / KBs / conhecimentos) ───────────────
  hasSessionAgent = false;
  activeKbs: KnowledgeBase[] = [];
  activeKnowledge: ActiveKnowledgeRef[] = [];
  saModalOpen = false;
  kbModalOpen = false;
  knowModalOpen = false;

  // ── Playbook ativo na conversa ────────────────────────────────────
  playbookModalOpen = false;
  activePlaybook: PlaybookSummary | null = null;
  playbookSuggestions: PlaybookSuggestion[] = [];
  // Vínculo pendente p/ conversa nova (sem id ainda): vai no payload do 1º
  // envio. undefined = nada pendente; null = desvincular; número = vincular.
  private pendingPlaybookId: number | null | undefined = undefined;

  private controller: AbortController | null = null;
  private activeConversationId: number | null = null;
  private activeExecutionId: string | null = null;
  private executionEventCursor = 0;
  private executionPollTimer: ReturnType<typeof setTimeout> | null = null;
  private shouldScroll = false;
  private followLatest = true;
  private lastMessagesScrollTop = 0;
  private readonly FOLLOW_LATEST_THRESHOLD = 64;
  private routeSub?: Subscription;

  constructor(
    private chat: ChatService,
    private config: ConfigService,
    private route: ActivatedRoute,
    private router: Router,
    private bus: ConversationBus,
    private playbookApi: PlaybookService,
  ) {}

  ngOnInit(): void {
    this.loadAgents();
    this.loadAtenaSkills();
    // Reage a mudanças no ?c=<id> (clique na sidebar, voltar/avançar do browser,
    // nova conversa). O snapshot único não bastava porque o componente não é
    // recriado ao navegar dentro da mesma rota /chat.
    this.routeSub = this.route.queryParamMap.subscribe((params) => {
      const id = params.get('c');
      const idNum = id ? Number(id) : null;
      // Já é a conversa carregada (ex.: acabamos de criá-la e escrever a URL) —
      // não recarrega, senão apagaria as mensagens que já estão na tela.
      if (idNum === this.conversationId) return;
      // Troca de conversa: aborta qualquer geração em curso para a resposta não
      // cair na conversa errada (o legado recarregava a página, matando tudo).
      if (this.controller) this.stop();
      if (idNum) this.loadConversation(idNum);
      else this.resetChat();
    });
  }

  private loadAtenaSkills(): void {
    this.chat.getCodexSkills().subscribe({
      next: ({ skills }) => {
        const prompts = new Map((skills as CodexSkillPrompt[]).map((skill) => [skill.slug, skill.content]));
        this.atenaSkills = this.atenaSkills.map((skill) => ({ ...skill, content: prompts.get(skill.slug) || skill.content }));
      },
    });
  }

  ngOnDestroy(): void {
    this.stopExecutionPolling();
    this.routeSub?.unsubscribe();
    document.body.classList.remove('html-preview-open', 'html-preview-full');
    this.cleanupLivePanel();
  }

  // Zera a tela para uma conversa nova (equivale a abrir /chat sem ?c).
  private resetChat(): void {
    this.stopExecutionPolling();
    this.activeExecutionId = null;
    this.activeConversationId = null;
    this.executionEventCursor = 0;
    this.isLoading = false;
    this.stopRequested = false;
    this.conversationId = null;
    this.messages = [];
    this.awaitingHuman = false;
    this.pendingInteraction = null;
    this.interactionAnswers = {};
    this.interactionError = '';
    this.livePlan = [];
    this.livePlanExplanation = '';
    this.hasSessionAgent = false;
    this.activeKbs = [];
    this.activeKnowledge = [];
    this.progress = [];
    this.resetLiveTree();
    this.typing = false;
    this.uploading = null;
    this.filesMenuOpen = false;
    this.skillsMenuOpen = false;
    this.expandedSkill = null;
    this.activePlaybook = null;
    this.playbookSuggestions = [];
    this.pendingPlaybookId = undefined;
    this.shouldScroll = false;
    this.followLatest = true;
    this.lastMessagesScrollTop = 0;
  }

  ngAfterViewChecked(): void {
    if (this.shouldScroll && this.messagesArea) {
      const el = this.messagesArea.nativeElement;
      el.scrollTop = el.scrollHeight;
      this.lastMessagesScrollTop = el.scrollTop;
      this.shouldScroll = false;
    }
  }

  get hasMessages(): boolean {
    return this.messages.length > 0 || this.typing || !!this.uploading;
  }

  get selectedAgent(): AgentInfo | undefined {
    return this.agents.find((a) => a.slug === this.selectedAgentSlug);
  }

  onMessagesScroll(): void {
    if (!this.messagesArea) return;
    const el = this.messagesArea.nativeElement;
    const scrollTop = el.scrollTop;
    const distanceFromBottom = el.scrollHeight - scrollTop - el.clientHeight;
    const userMovedUp = scrollTop < this.lastMessagesScrollTop - 1;

    if (userMovedUp) {
      // O usuário assumiu o controle para ler conteúdo anterior. Eventos do
      // agente continuam chegando, mas não alteram mais a posição da tela.
      this.followLatest = false;
      this.shouldScroll = false;
    } else if (distanceFromBottom <= this.FOLLOW_LATEST_THRESHOLD) {
      // Ao voltar ao final, o acompanhamento automático é reativado.
      this.followLatest = true;
    }

    this.lastMessagesScrollTop = scrollTop;
  }

  private scrollSoon(force = false): void {
    if (force) this.followLatest = true;
    if (this.followLatest) this.shouldScroll = true;
  }

  // ── Carga ────────────────────────────────────────────────────────
  private loadAgents(): void {
    this.config.getConfig().subscribe({
      next: (cfg) => {
        this.agents = cfg.agents || [];
        const def = this.agents.find((a) => a.is_default) || this.agents[0];
        if (!this.selectedAgentSlug && def) this.selectedAgentSlug = def.slug;
      },
      error: () => {},
    });
  }

  private loadConversation(id: number, terminalError = '', forceScroll = true): void {
    this.stopExecutionPolling();
    this.chat.getConversation(id).subscribe({
      next: (data) => {
        this.conversationId = data.id;
        if (data.agent_slug) this.selectedAgentSlug = data.agent_slug;
        this.messages = data.messages || [];
        this.awaitingHuman = data.awaiting_human_input;
        this.hasSessionAgent = data.has_session_agent;
        this.pendingPlaybookId = undefined;
        this.applyPlaybookBinding(data.playbook_id ?? null, data.playbook_name ?? null);
        this.loadConversationModals(data.id);
        if (terminalError) {
          this.messages.push({
            role: 'assistant',
            content: `❌ Execução encerrada: ${terminalError}`,
            tool_calls: [],
          });
        }
        if (data.active_execution) {
          this.recoverExecution(data.active_execution);
        } else if (!this.controller) {
          this.activeExecutionId = null;
          this.activeConversationId = null;
          this.executionEventCursor = 0;
          this.isLoading = false;
          this.typing = false;
          this.stopRequested = false;
          this.pendingInteraction = null;
        }
        this.scrollSoon(forceScroll);
      },
      error: () => {},
    });
  }

  private executionIsActive(execution: ExecutionSnapshot): boolean {
    return ['queued', 'starting', 'running', 'waiting_user', 'stopping'].includes(execution.status);
  }

  private recoverExecution(execution: ExecutionSnapshot): void {
    this.activeExecutionId = execution.id;
    this.activeConversationId = execution.conversation_id;
    this.executionEventCursor = 0;
    this.isLoading = this.executionIsActive(execution);
    this.typing = this.isLoading;
    this.stopRequested = execution.status === 'stopping';
    this.progress = [];
    this.resetLiveTree();
    this.pendingInteraction = null;
    this.interactionAnswers = {};
    this.interactionError = '';
    this.livePlan = execution.plan || [];
    this.livePlanExplanation = execution.plan_explanation || '';
    this.consumeExecutionEvents(execution.events || []);
    if (this.executionIsActive(execution)) {
      this.scheduleExecutionPoll(300);
    } else {
      this.finishRecoveredExecution(execution);
    }
  }

  private consumeExecutionEvents(events: ExecutionEvent[]): void {
    const hasSequence = events.some((event) => Number(event['sequence'] || 0) > 0);
    const pending = hasSequence
      ? events.filter((event) => Number(event['sequence'] || 0) > this.executionEventCursor)
      : events.slice(this.executionEventCursor);
    if (hasSequence) {
      this.executionEventCursor = events.reduce(
        (latest, event) => Math.max(latest, Number(event['sequence'] || 0)),
        this.executionEventCursor,
      );
    } else {
      this.executionEventCursor = events.length;
    }
    for (const event of pending) {
      if (event.type === 'progress') {
        const progressEvent = event as ChatProgressEvent;
        this.pushProgress(progressEvent);
        this.ingestLiveEvent(progressEvent);
      } else if (event.type === 'plan') {
        this.livePlan = event['plan'] || [];
        this.livePlanExplanation = event['explanation'] || '';
      } else if (event.type === 'interaction' && event['interaction']) {
        this.pendingInteraction = event['interaction'] as CodexInteraction;
        this.interactionAnswers = {};
        this.interactionError = '';
        this.awaitingHuman = true;
      } else if (event.type === 'interaction_resolved') {
        if (!event['token'] || this.pendingInteraction?.token === event['token']) {
          this.pendingInteraction = null;
          this.awaitingHuman = false;
        }
      }
    }
    this.scrollSoon();
  }

  private rememberLiveEventSequence(event: { sequence?: number }): void {
    const sequence = Number(event.sequence || 0);
    if (sequence > this.executionEventCursor) this.executionEventCursor = sequence;
  }

  private resumePersistentExecution(): void {
    this.controller = null;
    this.typing = true;
    this.pushProgress({
      stage: 'thinking',
      icon: '◈',
      text: 'Atena está preparando a execução…',
    });
    this.scheduleExecutionPoll(300);
    this.scrollSoon();
  }

  private scheduleExecutionPoll(delayMs = 1000): void {
    this.stopExecutionPolling();
    if (!this.activeExecutionId) return;
    this.executionPollTimer = setTimeout(() => this.pollExecution(), delayMs);
  }

  private pollExecution(): void {
    const executionId = this.activeExecutionId;
    if (!executionId) return;
    this.chat.getExecution(executionId).subscribe({
      next: ({ execution }) => {
        if (this.activeExecutionId !== executionId) return;
        this.consumeExecutionEvents(execution.events || []);
        this.livePlan = execution.plan || this.livePlan;
        this.livePlanExplanation = execution.plan_explanation || this.livePlanExplanation;
        this.awaitingHuman = execution.status === 'waiting_user';
        this.stopRequested = execution.status === 'stopping';
        if (this.executionIsActive(execution)) {
          this.isLoading = true;
          this.typing = true;
          this.scheduleExecutionPoll();
        } else {
          this.finishRecoveredExecution(execution);
        }
      },
      error: () => this.scheduleExecutionPoll(2000),
    });
  }

  private finishRecoveredExecution(execution: ExecutionSnapshot): void {
    const conversationId = execution.conversation_id;
    this.stopExecutionPolling();
    this.activeExecutionId = null;
    this.activeConversationId = null;
    this.executionEventCursor = 0;
    this.isLoading = false;
    this.typing = false;
    this.stopRequested = false;
    this.pendingInteraction = null;
    this.awaitingHuman = false;
    this.finalizeLiveTree();
    this.bus.notifyChanged();
    this.loadConversation(
      conversationId,
      execution.status === 'failed' ? (execution.error || 'falha sem detalhe') : '',
      false,
    );
  }

  private stopExecutionPolling(): void {
    if (this.executionPollTimer) {
      clearTimeout(this.executionPollTimer);
      this.executionPollTimer = null;
    }
  }

  // Reflete o playbook vinculado à conversa: badge + sugestões. Busca o detalhe
  // (ícone/sugestões) quando há vínculo; limpa quando não há.
  private applyPlaybookBinding(id: number | null, name: string | null): void {
    if (id == null) {
      this.activePlaybook = null;
      this.playbookSuggestions = [];
      return;
    }
    // Placeholder imediato (nome do detalhe) enquanto o detalhe completo carrega.
    this.activePlaybook = {
      id, name: name || 'Playbook', description: '', icon: '📐', node_count: 0,
      stage_count: 0, status: 'draft', version: 1, revision_count: 0,
    };
    this.playbookApi.get(id).subscribe({
      next: (res) => {
        const pb = res.playbook;
        this.activePlaybook = {
          id: pb.id,
          name: pb.name,
          description: pb.description,
          icon: pb.icon || '📐',
          node_count: (pb.nodes || []).length,
          stage_count: (pb.nodes || []).filter((node) => !node.is_root).length || 1,
          status: pb.status,
          version: pb.version,
          revision_count: pb.revision_count,
        };
        this.playbookSuggestions = pb.suggestions || [];
      },
      error: () => {},
    });
  }

  // Carrega KBs e conhecimentos ativos da conversa (para pré-marcar os modais
  // e mostrar os badges). Espelha loadConversationKbs/Knowledge do chat.js.
  private loadConversationModals(id: number): void {
    this.chat.getConversationKbs(id).subscribe({
      next: (data) => (this.activeKbs = data.active_kbs || []),
      error: () => {},
    });
    this.chat.getConversationKnowledge(id).subscribe({
      next: (data) => (this.activeKnowledge = data.active_knowledge || []),
      error: () => {},
    });
  }

  fillSuggestion(text: string): void {
    this.input = text;
  }

  // ── Envio de mensagem ────────────────────────────────────────────
  onKeydown(event: KeyboardEvent): void {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      this.send();
    }
  }

  async send(): Promise<void> {
    const text = this.input.trim();
    if (!text || this.isLoading) return;

    this.isLoading = true;
    this.stopExecutionPolling();
    this.controller = new AbortController();
    this.activeConversationId = this.conversationId;
    this.activeExecutionId = null;
    this.executionEventCursor = 0;
    this.stopRequested = false;
    this.input = '';

    this.messages.push({ role: 'user', content: text, tool_calls: [] });
    this.typing = true;
    this.progress = [];
    this.resetLiveTree();
    this.pendingInteraction = null;
    this.interactionAnswers = {};
    this.interactionError = '';
    this.livePlan = [];
    this.livePlanExplanation = '';
    this.scrollSoon(true);

    try {
      const data = await this.chat.stream(
        {
          message: text,
          conversation_id: this.conversationId,
          agent_slug: this.selectedAgentSlug,
          engine: this.engine,
          active_kbs: this.activeKbs,
          active_knowledge: this.activeKnowledge,
          // Só enviado quando há vínculo pendente (conversa nova); nas demais
          // vezes fica undefined → backend não mexe no binding já persistido.
          ...(this.pendingPlaybookId !== undefined ? { playbook_id: this.pendingPlaybookId } : {}),
        },
        {
          signal: this.controller.signal,
          onStarted: (conversationId, executionId) => {
            const wasNew = !this.conversationId;
            this.activeConversationId = conversationId;
            this.activeExecutionId = executionId;
            if (wasNew) {
              this.conversationId = conversationId;
              this.syncUrl(conversationId);
              this.bus.notifyChanged();
            }
            if (this.stopRequested) void this.stopActiveExecution();
          },
          onProgress: (evt) => {
            this.rememberLiveEventSequence(evt);
            this.pushProgress(evt);
            this.ingestLiveEvent(evt);
          },
          onInteraction: (interaction) => {
            this.pendingInteraction = interaction;
            this.interactionAnswers = {};
            this.interactionError = '';
            this.awaitingHuman = true;
            this.scrollSoon();
          },
          onInteractionResolved: (evt) => {
            this.rememberLiveEventSequence(evt);
            if (!evt.token || this.pendingInteraction?.token === evt.token) {
              this.pendingInteraction = null;
              this.awaitingHuman = false;
            }
          },
          onPlan: (evt: CodexPlanEvent) => {
            this.rememberLiveEventSequence(evt);
            this.livePlan = evt.plan || [];
            this.livePlanExplanation = evt.explanation || '';
            this.scrollSoon();
          },
        },
      );

      if ((!data || data.status !== 'success') && this.activeExecutionId && this.activeConversationId) {
        this.resumePersistentExecution();
        return;
      }

      this.typing = false;
      this.pendingInteraction = null;
      this.awaitingHuman = false;
      // Marca como concluído qualquer nó que tenha ficado "processando" (ex.:
      // o agente respondeu direto sem emitir tool_result para tudo).
      this.finalizeLiveTree();

      if (data && data.status === 'success') {
        const reply = data.reply || ({ role: 'assistant', content: '' } as ChatMessage);
        this.messages.push({
          role: 'assistant',
          content: reply.content || '',
          tool_calls: reply.tool_calls || [],
          attachment: reply.attachment || null,
          attachments: reply.attachments || [],
        });
        const wasNew = !this.conversationId;
        this.conversationId = data.conversation_id;
        // O binding pendente já foi aplicado pelo backend neste turno.
        this.pendingPlaybookId = undefined;
        this.awaitingHuman = !!data.awaiting_human_input;
        // Conversa recém-criada: reescreve a URL p/ ?c=<id> (sem recarregar) e
        // atualiza a sidebar. Espelha o history.replaceState + refreshHistory
        // do chat.js legado.
        if (wasNew && this.conversationId) this.syncUrl(this.conversationId);
        this.bus.notifyChanged();
      } else {
        const msg = (data && data.message) || 'Ocorreu um erro.';
        this.messages.push({ role: 'assistant', content: '❌ Erro: ' + msg, tool_calls: [] });
      }
    } catch (err: any) {
      if (err?.name === 'AbortError') {
        this.typing = false;
        this.messages.push({ role: 'assistant', content: '⏹️ Geração interrompida.', tool_calls: [] });
      } else if (this.activeExecutionId && this.activeConversationId) {
        this.resumePersistentExecution();
        return;
      } else {
        this.typing = false;
        this.messages.push({
          role: 'assistant',
          content: '❌ Falha na conexão: ' + (err?.message || err),
          tool_calls: [],
        });
      }
    }

    this.isLoading = false;
    this.controller = null;
    this.activeConversationId = null;
    this.activeExecutionId = null;
    this.executionEventCursor = 0;
    this.stopRequested = false;
    this.scrollSoon();
  }

  get interactionQuestionsComplete(): boolean {
    const questions = this.pendingInteraction?.questions || [];
    return questions.length > 0 && questions.every((question) => !!this.interactionAnswers[question.id]?.trim());
  }

  planIcon(status: CodexPlanItem['status']): string {
    if (status === 'completed') return '✓';
    if (status === 'failed') return '×';
    if (status === 'skipped') return '–';
    if (status === 'inProgress') return '●';
    return '○';
  }

  get planProgressLabel(): string {
    const current = this.livePlan.findIndex((item) => item.status === 'inProgress');
    if (current >= 0) return `Etapa ${current + 1} de ${this.livePlan.length}`;
    const completed = this.livePlan.filter((item) => item.status === 'completed').length;
    return completed === this.livePlan.length && completed > 0
      ? `${completed} etapas concluídas`
      : `${completed} de ${this.livePlan.length}`;
  }

  async submitInteractionAnswers(): Promise<void> {
    if (!this.pendingInteraction || !this.interactionQuestionsComplete) return;
    const answers: Record<string, string[]> = {};
    for (const question of this.pendingInteraction.questions || []) {
      answers[question.id] = [this.interactionAnswers[question.id].trim()];
    }
    await this.respondToInteraction({ answers });
  }

  async approveInteraction(decision: 'accept' | 'acceptForSession' | 'decline' | 'cancel'): Promise<void> {
    await this.respondToInteraction({ decision });
  }

  async approvePermissions(approve: boolean, scope: 'turn' | 'session' = 'turn'): Promise<void> {
    await this.respondToInteraction({ approve, scope });
  }

  async approveAllInteraction(): Promise<void> {
    await this.respondToInteraction({ approve_all: true });
  }

  private async respondToInteraction(payload: Record<string, unknown>): Promise<void> {
    const interaction = this.pendingInteraction;
    if (!interaction || this.interactionSubmitting) return;
    this.interactionSubmitting = true;
    this.interactionError = '';
    try {
      await this.chat.respondToCodexInteraction(interaction.token, payload);
      this.pendingInteraction = null;
      this.awaitingHuman = false;
    } catch (err: any) {
      this.interactionError = err?.message || String(err);
    } finally {
      this.interactionSubmitting = false;
      this.scrollSoon();
    }
  }

  stop(): void {
    if ((!this.controller && !this.activeExecutionId) || this.stopRequested) return;
    this.stopRequested = true;
    this.cancelPendingInteraction();
    if (this.activeExecutionId || this.activeConversationId) void this.stopActiveExecution();
  }

  private async stopActiveExecution(): Promise<void> {
    const controller = this.controller;
    const conversationId = this.activeConversationId;
    const executionId = this.activeExecutionId;
    if (!executionId && !conversationId) return;
    try {
      const result = executionId
        ? await this.chat.stopExecution(executionId)
        : await this.chat.stopConversation(conversationId!);
      if (result.stopped && this.controller === controller) {
        controller?.abort();
        if (!controller) this.scheduleExecutionPoll(200);
      } else if (!result.stopped) {
        this.stopRequested = false;
      }
    } catch (err: any) {
      this.stopRequested = false;
      this.pushProgress({
        stage: 'thinking',
        icon: '⚠️',
        text: `Não foi possível interromper no servidor: ${err?.message || err}`,
      });
      this.scrollSoon();
    }
  }

  private cancelPendingInteraction(): void {
    const interaction = this.pendingInteraction;
    if (!interaction) return;
    this.pendingInteraction = null;
    this.awaitingHuman = false;
    void this.chat.respondToCodexInteraction(interaction.token, { cancel: true }).catch(() => {});
  }

  // Escreve ?c=<id> na URL sem recarregar nem recriar o componente. replaceUrl
  // para não empilhar histórico do browser (o legado usava replaceState).
  // A guarda em ngOnInit evita que essa navegação recarregue a conversa.
  private syncUrl(id: number): void {
    this.router.navigate([], {
      relativeTo: this.route,
      queryParams: { c: id },
      replaceUrl: true,
    });
  }

  // ── Anexos (render) ──────────────────────────────────────────────
  // Junta o anexo único (attachment) e a lista de artefatos (attachments)
  // numa só lista, como o appendAttachmentCard faz no chat.js.
  attachmentsOf(m: ChatMessage): Attachment[] {
    const out: Attachment[] = [];
    if (m.attachment) out.push(m.attachment);
    (m.attachments || []).forEach((a) => out.push(a));
    return out;
  }

  // Entrada da conversa: arquivos enviados pelo usuário e já materializados
  // na pasta `entrada/` da sessão pelo backend.
  get inputFiles(): Array<{
    filename: string;
    kind: 'table' | 'document';
    meta: string;
  }> {
    const files = new Map<string, { filename: string; kind: 'table' | 'document'; meta: string }>();
    for (const message of this.messages) {
      if (message.role !== 'user') continue;
      for (const attachment of this.attachmentsOf(message)) {
        if (attachment.kind !== 'table' && attachment.kind !== 'document') continue;
        const filename = String(attachment['filename'] || '').trim();
        if (!filename) continue;
        let meta = 'Documento';
        if (attachment.kind === 'table') {
          const rows = Number(attachment['rows'] || 0);
          const columns = Array.isArray(attachment['columns']) ? attachment['columns'].length : 0;
          meta = `${rows.toLocaleString('pt-BR')} linhas · ${columns} colunas`;
        } else {
          const pages = Number(attachment['page_count'] || 0);
          const chars = Number(attachment['char_count'] || 0);
          meta = pages ? `${pages} página${pages === 1 ? '' : 's'}` : `${chars.toLocaleString('pt-BR')} caracteres`;
        }
        // A ocorrência mais recente do mesmo nome representa o arquivo ativo.
        files.set(filename, { filename, kind: attachment.kind, meta });
      }
    }
    return Array.from(files.values()).reverse();
  }

  // Saídas persistidas na pasta `saida/` desta conversa. A URL é própria
  // do chat, então o menu não mistura arquivos de conversas diferentes.
  get outputFiles(): Array<{
    filename: string;
    format: string;
    meta: string;
    downloadUrl: string;
  }> {
    const files = new Map<string, {
      filename: string;
      format: string;
      meta: string;
      downloadUrl: string;
    }>();
    for (const message of this.messages) {
      if (message.role !== 'assistant') continue;
      for (const attachment of this.attachmentsOf(message)) {
        if (attachment.kind !== 'export') continue;
        const filename = String(attachment['filename'] || '').trim();
        const downloadUrl = String(attachment['download_url'] || '').trim();
        if (!filename || !downloadUrl) continue;
        const format = String(attachment['formato'] || '').toLowerCase();
        const sizeKb = Number(attachment['size_kb'] || 0);
        const detail = format ? format.toUpperCase() : 'Arquivo';
        const size = sizeKb > 0 ? ` · ${sizeKb >= 1024 ? `${(sizeKb / 1024).toFixed(1)} MB` : `${sizeKb} KB`}` : '';
        files.set(downloadUrl, { filename, format, meta: `${detail}${size}`, downloadUrl });
      }
    }
    return Array.from(files.values()).reverse();
  }

  get sessionFileCount(): number {
    return this.inputFiles.length + this.outputFiles.length;
  }

  toggleFilesMenu(): void {
    this.filesMenuOpen = !this.filesMenuOpen;
    if (this.filesMenuOpen) this.attachMenuOpen = false;
  }

  toggleSkillsMenu(): void {
    this.skillsMenuOpen = !this.skillsMenuOpen;
    if (!this.skillsMenuOpen) this.expandedSkill = null;
    if (this.skillsMenuOpen) {
      this.attachMenuOpen = false;
      this.filesMenuOpen = false;
    }
  }

  toggleSkillContent(slug: string): void {
    this.expandedSkill = this.expandedSkill === slug ? null : slug;
  }

  fileIcon(kind: 'table' | 'document'): string {
    return kind === 'table' ? '📊' : '📄';
  }

  outputIcon(format: string): string {
    if (format === 'html' || format === 'htm') return '🌐';
    if (format === 'pdf') return '📕';
    if (format === 'xlsx') return '📊';
    if (format === 'csv') return '🧾';
    return '📦';
  }

  asTable(att: Attachment): TableAttachment {
    return att as unknown as TableAttachment;
  }

  asDocument(att: Attachment): DocumentAttachment {
    return att as unknown as DocumentAttachment;
  }

  asExport(att: Attachment): ExportAttachment {
    return att as unknown as ExportAttachment;
  }

  asChart(att: Attachment): ChartAttachment {
    return att as unknown as ChartAttachment;
  }

  asMermaid(att: Attachment): MermaidAttachment {
    return att as unknown as MermaidAttachment;
  }

  // ── Upload ───────────────────────────────────────────────────────
  toggleAttachMenu(): void {
    this.attachMenuOpen = !this.attachMenuOpen;
  }

  @HostListener('document:click', ['$event'])
  onDocClick(ev: MouseEvent): void {
    const target = ev.target as HTMLElement;
    // Fecha os menus ao clicar fora deles.
    if (this.attachMenuOpen && !target.closest('.attach-wrap')) this.attachMenuOpen = false;
    if (this.filesMenuOpen && !target.closest('.files-wrap')) this.filesMenuOpen = false;
    if (this.skillsMenuOpen && !target.closest('.skills-wrap')) {
      this.skillsMenuOpen = false;
      this.expandedSkill = null;
    }
  }

  pickSingle(): void {
    this.attachMenuOpen = false;
    this.fileInput?.nativeElement.click();
  }

  pickBatchFiles(): void {
    this.attachMenuOpen = false;
    this.batchFilesInput?.nativeElement.click();
  }

  pickBatchFolder(): void {
    this.attachMenuOpen = false;
    this.batchFolderInput?.nativeElement.click();
  }

  onSingleFiles(ev: Event): void {
    const input = ev.target as HTMLInputElement;
    const files = Array.from(input.files || []);
    input.value = '';
    if (files.length) this.uploadTablesSequentially(files);
  }

  onBatchFiles(ev: Event): void {
    const input = ev.target as HTMLInputElement;
    const files = Array.from(input.files || []);
    input.value = '';
    if (files.length) this.uploadBatch(files);
  }

  private async uploadTablesSequentially(files: File[]): Promise<void> {
    for (const file of files) {
      await this.uploadSingle(file);
    }
  }

  private async uploadSingle(file: File): Promise<void> {
    if (this.isLoading) return;
    const note = this.input.trim();
    this.input = '';
    this.isLoading = true;
    this.uploading = `Carregando ${file.name}…`;
    this.scrollSoon(true);
    try {
      const data = await this.chat.uploadTable(file, {
        conversationId: this.conversationId,
        agentSlug: this.selectedAgentSlug,
        note,
      });
      this.handleUploadResult(data);
    } catch (err: any) {
      this.messages.push({ role: 'assistant', content: '❌ Falha no upload: ' + (err?.message || err), tool_calls: [] });
    }
    this.uploading = null;
    this.isLoading = false;
    this.scrollSoon();
  }

  private async uploadBatch(files: File[]): Promise<void> {
    if (this.isLoading) return;
    if (files.length > this.BATCH_MAX_FILES) {
      this.messages.push({
        role: 'assistant',
        content: `❌ Máximo de ${this.BATCH_MAX_FILES} arquivos por vez (você selecionou ${files.length}).`,
        tool_calls: [],
      });
      return;
    }
    const note = this.input.trim();
    this.input = '';
    this.isLoading = true;
    const label = files.length === 1 ? files[0].name : `${files.length} documentos`;
    this.uploading = `Carregando ${label}…`;
    this.scrollSoon(true);
    try {
      const data = await this.chat.uploadBatch(files, {
        conversationId: this.conversationId,
        agentSlug: this.selectedAgentSlug,
        note,
        onProgress: (evt) => {
          if (evt.total) {
            const nome = evt.filename ? ` ${evt.filename}` : '';
            this.uploading = `Extraindo ${evt.done} de ${evt.total}${nome}…`;
          }
        },
      });
      this.handleUploadResult(data);
    } catch (err: any) {
      this.messages.push({ role: 'assistant', content: '❌ Falha no upload: ' + (err?.message || err), tool_calls: [] });
    }
    this.uploading = null;
    this.isLoading = false;
    this.scrollSoon();
  }

  private handleUploadResult(data: UploadResult): void {
    if (data && data.status === 'success' && typeof data.message !== 'string') {
      const wasNew = !this.conversationId;
      if (data.conversation_id) this.conversationId = data.conversation_id;
      if (data.agent_slug) this.selectedAgentSlug = data.agent_slug;
      if (data.message) this.messages.push(data.message as ChatMessage);
      if (wasNew && this.conversationId) this.syncUrl(this.conversationId);
      this.bus.notifyChanged();
    } else {
      const msg = (data && (typeof data.message === 'string' ? data.message : '')) || 'erro desconhecido';
      this.messages.push({ role: 'assistant', content: '❌ Falha no upload: ' + msg, tool_calls: [] });
    }
  }

  // ── Progresso ao vivo ────────────────────────────────────────────
  // Reproduz o updateTypingProgress do chat.js: eventos "massiva" atualizam
  // uma única linha (barra textual); os demais empilham, mantendo os últimos 5.
  private pushProgress(evt: { stage?: string; icon?: string; text?: string; current?: number; total?: number }): void {
    // "tool_result" é um evento de término consumido só pelo painel de
    // execução ao vivo (não tem texto próprio) — não vai para o log flat.
    if (evt.stage === 'tool_result') return;
    const icon = evt.icon || (evt.stage === 'thinking' ? '🧠' : evt.stage === 'massiva' ? '🚀' : '⚙️');
    const text = evt.text || '';

    if (evt.stage === 'massiva') {
      const pct = evt.total ? Math.round(((evt.current || 0) / evt.total) * 100) : 0;
      const existing = this.progress.find((p) => p.massiva);
      if (existing) {
        existing.icon = icon;
        existing.text = text;
        existing.pct = pct;
      } else {
        this.progress.push({ icon, text, massiva: true, pct });
      }
    } else {
      this.progress.push({ icon, text, massiva: false });
      while (this.progress.filter((p) => !p.massiva).length > 5) {
        const idx = this.progress.findIndex((p) => !p.massiva);
        if (idx >= 0) this.progress.splice(idx, 1);
      }
    }
    this.scrollSoon();
  }

  // ── Execução ao vivo (árvore incremental) ─────────────────────────
  private resetLiveTree(): void {
    this.liveTree = [];
    this.liveIndex.clear();
    // Não fecha o painel: se estava aberto de um turno anterior, a árvore
    // nova simplesmente o repovoa. O usuário fecha quando quiser.
  }

  // Consome um evento de progresso e atualiza a árvore. Ignora silenciosamente
  // eventos sem dados de árvore (ex.: 'thinking', 'massiva') — o log flat já
  // cuida deles.
  ingestLiveEvent(evt: ChatProgressEvent): void {
    if (evt.stage === 'tool' && evt.tool_call_id) {
      // Início de uma tool: cria o nó se ainda não existe.
      if (this.liveIndex.has(evt.tool_call_id)) return;
      const node: LiveNode = {
        id: evt.tool_call_id,
        parentId: evt.parent_id ?? null,
        agent: evt.agent || '',
        tool: evt.tool || '',
        icon: evt.icon || '⚡',
        label: evt.text || evt.tool || 'tool',
        args: evt.args || {},
        status: 'running',
        children: [],
      };
      this.liveIndex.set(node.id, node);
      const parent = node.parentId ? this.liveIndex.get(node.parentId) : undefined;
      if (parent) parent.children.push(node);
      else this.liveTree.push(node);
    } else if (evt.stage === 'tool_result' && evt.tool_call_id) {
      // Término: fecha o nó (resultado/erro/duração).
      const node = this.liveIndex.get(evt.tool_call_id);
      if (!node) return;
      node.status = evt.error ? 'error' : 'done';
      node.error = evt.error || undefined;
      node.durationMs = evt.duration_ms;
      node.resultPreview = evt.result_preview || undefined;
    }
  }

  // Ao fim do turno, marca como concluído o que sobrou "processando".
  private finalizeLiveTree(): void {
    for (const node of this.liveIndex.values()) {
      if (node.status === 'running') node.status = 'done';
    }
  }

  // Nº de tools na árvore (para o rótulo do indicador clicável).
  get liveCount(): number {
    return this.liveIndex.size;
  }

  get liveRunning(): boolean {
    for (const node of this.liveIndex.values()) {
      if (node.status === 'running') return true;
    }
    return false;
  }

  // ── Painel lateral (estilo artifacts) ─────────────────────────────
  openLivePanel(): void {
    if (!this.liveTree.length) return;
    this.livePanelOpen = true;
    document.body.classList.add('html-preview-open');
    setTimeout(() => this.moveLivePanelToBody());
  }

  private moveLivePanelToBody(): void {
    const el = this.livePanelRef?.nativeElement;
    if (el && el.parentElement !== document.body) {
      document.body.appendChild(el);
      this.liveMovedPanel = el;
    }
  }

  private cleanupLivePanel(): void {
    if (this.liveMovedPanel && this.liveMovedPanel.parentElement === document.body) {
      this.liveMovedPanel.remove();
    }
    this.liveMovedPanel = null;
  }

  closeLivePanel(): void {
    this.livePanelOpen = false;
    this.livePanelFull = false;
    document.body.classList.remove('html-preview-open', 'html-preview-full');
    this.cleanupLivePanel();
  }

  toggleLiveFull(): void {
    this.livePanelFull = !this.livePanelFull;
    document.body.classList.toggle('html-preview-full', this.livePanelFull);
  }

  @HostListener('document:keydown.escape')
  onLiveEsc(): void {
    if (this.livePanelFull) this.toggleLiveFull();
    else if (this.livePanelOpen) this.closeLivePanel();
  }

  // ── Modais: abertura ──────────────────────────────────────────────
  openSessionAgent(): void {
    this.saModalOpen = true;
  }

  openKbs(): void {
    this.kbModalOpen = true;
  }

  openKnowledge(): void {
    this.knowModalOpen = true;
  }

  openPlaybookPicker(): void {
    this.playbookModalOpen = true;
  }

  get activePlaybookId(): number | null {
    return this.activePlaybook ? this.activePlaybook.id : null;
  }

  // Usuário escolheu um playbook (ou "Nenhum" → pb=null) no modal.
  onPlaybookSelected(pb: PlaybookSummary | null): void {
    const id = pb ? pb.id : null;
    if (this.conversationId) {
      // Conversa existente: grava o vínculo já.
      this.playbookApi.bindConversation(this.conversationId, id).subscribe({ error: () => {} });
      this.pendingPlaybookId = undefined;
    } else {
      // Conversa nova: vai no payload do 1º envio.
      this.pendingPlaybookId = id;
    }
    if (pb) {
      this.activePlaybook = pb;
      // Carrega as sugestões do playbook escolhido.
      this.playbookApi.get(pb.id).subscribe({
        next: (res) => (this.playbookSuggestions = res.playbook.suggestions || []),
        error: () => (this.playbookSuggestions = []),
      });
    } else {
      this.activePlaybook = null;
      this.playbookSuggestions = [];
    }
  }

  get kbCount(): number {
    return this.activeKbs.length;
  }

  get knowCount(): number {
    return this.activeKnowledge.length;
  }

  // ── Modais: callbacks ─────────────────────────────────────────────
  onSaSaved(evt: { conversationId: number | null; agent: SessionAgent }): void {
    // create-conversation pode ter gerado uma conversa nova.
    const wasNew = !this.conversationId;
    if (evt.conversationId && !this.conversationId) {
      this.conversationId = evt.conversationId;
    }
    this.hasSessionAgent = true;
    if (wasNew && this.conversationId) this.syncUrl(this.conversationId);
    this.bus.notifyChanged();
  }

  onSaDeleted(): void {
    this.hasSessionAgent = false;
  }

  onKbsSaved(selected: KnowledgeBase[]): void {
    this.activeKbs = selected;
    // Persiste só se a conversa já existe; senão vai no payload do próximo envio.
    if (this.conversationId) {
      this.chat.saveConversationKbs(this.conversationId, selected).subscribe({ error: () => {} });
    }
  }

  onKnowledgeSaved(selected: ActiveKnowledgeRef[]): void {
    this.activeKnowledge = selected;
    if (this.conversationId) {
      this.chat.saveConversationKnowledge(this.conversationId, selected).subscribe({ error: () => {} });
    }
  }

  trackMessage = (i: number) => i;
  trackProgress = (i: number) => i;
}
