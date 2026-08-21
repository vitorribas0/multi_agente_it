import {
  Component,
  ElementRef,
  EventEmitter,
  Input,
  OnInit,
  Output,
  ViewChild,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';

import { ConfigService } from '../../api/config.service';
import { ToolInfo } from '../../api/config.models';
import { PlaybookService } from '../../api/playbook.service';
import {
  PlaybookDetail,
  PlaybookEdge,
  PlaybookNode,
  PlaybookSuggestion,
} from '../../api/playbook.models';

type Tab = 'canvas' | 'suggestions';

// Editor de canvas de um playbook: nós arrastáveis, arestas de delegação
// (clique na porta de saída → clique no nó destino), painel lateral do nó
// selecionado (prompt/modelo/temperatura/tools) e aba de sugestões.
// Guarda um id de cliente por nó (n.id) para casar as arestas antes de o
// backend canonizar os slugs; ao salvar, o backend devolve os slugs canônicos.
@Component({
  selector: 'app-playbook-editor',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './playbook-editor.component.html',
  styleUrls: ['./playbook-editor.component.css'],
})
export class PlaybookEditorComponent implements OnInit {
  // id do playbook a editar; null = novo.
  @Input() playbookId: number | null = null;
  @Output() closed = new EventEmitter<void>();
  @Output() saved = new EventEmitter<void>();

  @ViewChild('canvas') canvasRef?: ElementRef<HTMLElement>;
  @ViewChild('fileInput') fileInputRef?: ElementRef<HTMLInputElement>;

  // Meta
  name = '';
  description = '';
  icon = '📘';

  // Grafo
  nodes: PlaybookNode[] = [];
  edges: PlaybookEdge[] = [];
  suggestions: PlaybookSuggestion[] = [];

  // Inventário para os selects/checkboxes
  models: string[] = [];
  tools: ToolInfo[] = [];

  // UI
  tab: Tab = 'canvas';
  selectedId: string | null = null;
  loading = true;
  saving = false;
  status = '';
  warnings: string[] = [];

  // Estado de arraste de nó
  private dragId: string | null = null;
  private dragDX = 0;
  private dragDY = 0;

  // ── Zoom & Pan do canvas ───────────────────────────────────────────
  scale = 1;
  panX = 0;
  panY = 0;
  private readonly MIN_SCALE = 0.3;
  private readonly MAX_SCALE = 2.5;
  panning = false;
  private panSX = 0;
  private panSY = 0;
  private panOX = 0;
  private panOY = 0;
  private panMoved = false;

  // Estado de ligação (criar aresta): slug/id do nó de origem em modo conexão
  linkingFrom: string | null = null;

  private seq = 1;

  constructor(
    private config: ConfigService,
    private api: PlaybookService,
  ) {}

  ngOnInit(): void {
    this.config.getConfig().subscribe({
      next: (cfg) => {
        this.models = cfg.models || [];
        this.tools = cfg.tools || [];
        if (this.playbookId) this.loadPlaybook(this.playbookId);
        else {
          this.seedNew();
          this.loading = false;
        }
      },
      error: () => {
        this.loading = false;
      },
    });
  }

  private loadPlaybook(id: number): void {
    this.api.get(id).subscribe({
      next: (data) => {
        this.applyDetail(data.playbook);
        this.loading = false;
      },
      error: () => {
        this.loading = false;
      },
    });
  }

  // Popula o editor a partir de um PlaybookDetail (vindo do backend ao carregar
  // ou salvar). Adota o id canônico quando presente e reusa o slug como id de
  // cliente (estável), para as arestas casarem.
  private applyDetail(pb: PlaybookDetail): void {
    if (typeof pb.id === 'number') this.playbookId = pb.id;
    this.name = pb.name;
    this.description = pb.description;
    this.icon = pb.icon || '📘';
    this.nodes = (pb.nodes || []).map((n) => ({ ...n, id: n.slug }));
    this.edges = (pb.edges || []).map((e) => ({ ...e }));
    this.suggestions = (pb.suggestions || []).map((s) => ({ ...s }));
  }

  // Playbook novo já nasce com um orquestrador root para não começar vazio.
  private seedNew(): void {
    this.name = '';
    this.description = '';
    this.icon = '📘';
    const root = this.blankNode('Orquestrador', '🧭', true);
    root.tools_enabled = this.tools.some((t) => t.slug === 'call_agent') ? ['call_agent'] : [];
    root.canvas = { x: 60, y: 60 };
    this.nodes = [root];
    this.edges = [];
    this.suggestions = [];
    this.selectedId = root.id!;
  }

  private newId(): string {
    return `n${this.seq++}_${Date.now() % 100000}`;
  }

  private blankNode(name: string, icon: string, isRoot: boolean): PlaybookNode {
    return {
      id: this.newId(),
      slug: '',
      name,
      description: '',
      icon,
      system_prompt: '',
      model: this.models[0] || 'gpt-4o',
      temperature: 0.7,
      tools_enabled: [],
      is_root: isRoot,
      canvas: { x: 120, y: 120 },
    };
  }

  // ── Nós ────────────────────────────────────────────────────────────
  addNode(): void {
    const n = this.blankNode('Especialista', '🤖', false);
    // Posição em cascata para não empilhar no mesmo ponto.
    const offset = this.nodes.length * 28;
    n.canvas = { x: 300 + (offset % 200), y: 80 + offset };
    this.nodes.push(n);
    this.selectedId = n.id!;
  }

  get selected(): PlaybookNode | undefined {
    return this.nodes.find((n) => n.id === this.selectedId);
  }

  selectNode(n: PlaybookNode): void {
    // Em modo ligação, um clique no nó fecha a aresta em vez de selecionar.
    if (this.linkingFrom) {
      this.completeLink(n);
      return;
    }
    this.selectedId = n.id!;
  }

  removeNode(n: PlaybookNode): void {
    if (n.is_root) {
      this.status = '❌ O nó root não pode ser removido (torne outro nó root antes).';
      return;
    }
    this.nodes = this.nodes.filter((x) => x.id !== n.id);
    this.edges = this.edges.filter((e) => e.source !== n.id && e.target !== n.id);
    if (this.selectedId === n.id) this.selectedId = null;
  }

  makeRoot(n: PlaybookNode): void {
    this.nodes.forEach((x) => (x.is_root = x.id === n.id));
  }

  // ── Arraste ────────────────────────────────────────────────────────
  onNodeMouseDown(ev: MouseEvent, n: PlaybookNode): void {
    if (this.linkingFrom) return; // em modo ligação não arrasta
    ev.stopPropagation();
    this.dragId = n.id!;
    const w = this.screenToWorld(ev.clientX, ev.clientY);
    this.dragDX = w.x - n.canvas.x;
    this.dragDY = w.y - n.canvas.y;
    this.selectedId = n.id!;
  }

  onCanvasMouseMove(ev: MouseEvent): void {
    // Pan da tela (arraste do fundo).
    if (this.panning) {
      this.panX = this.panOX + (ev.clientX - this.panSX);
      this.panY = this.panOY + (ev.clientY - this.panSY);
      if (Math.abs(ev.clientX - this.panSX) + Math.abs(ev.clientY - this.panSY) > 3) {
        this.panMoved = true;
      }
      return;
    }
    // Arraste de um nó (em coordenadas de mundo, compensando zoom/pan).
    if (!this.dragId) return;
    const n = this.nodes.find((x) => x.id === this.dragId);
    if (!n) return;
    const w = this.screenToWorld(ev.clientX, ev.clientY);
    n.canvas = {
      x: Math.max(0, w.x - this.dragDX),
      y: Math.max(0, w.y - this.dragDY),
    };
  }

  onCanvasMouseUp(): void {
    this.dragId = null;
    this.panning = false;
  }

  // Início do pan: mousedown no fundo do canvas (nós/portas dão stopPropagation).
  onCanvasMouseDown(ev: MouseEvent): void {
    if (this.linkingFrom) return;
    this.panning = true;
    this.panMoved = false;
    this.panSX = ev.clientX;
    this.panSY = ev.clientY;
    this.panOX = this.panX;
    this.panOY = this.panY;
  }

  // Clique no vazio do canvas cancela a ligação / deseleciona.
  onCanvasClick(): void {
    if (this.panMoved) {
      this.panMoved = false;
      return; // foi um pan, não um clique
    }
    if (this.linkingFrom) this.linkingFrom = null;
    else this.selectedId = null;
  }

  // ── Zoom ───────────────────────────────────────────────────────────
  get worldTransform(): string {
    return `translate(${this.panX}px, ${this.panY}px) scale(${this.scale})`;
  }

  get zoomPct(): number {
    return Math.round(this.scale * 100);
  }

  // Grade de fundo acompanha o pan/zoom para dar sensação de "tela infinita".
  get gridPosition(): string {
    return `${this.panX}px ${this.panY}px`;
  }

  get gridSize(): string {
    const s = 22 * this.scale;
    return `${s}px ${s}px`;
  }

  // Converte coordenadas de tela (clientX/Y) para o espaço do mundo.
  private screenToWorld(clientX: number, clientY: number): { x: number; y: number } {
    const rect = this.canvasRef?.nativeElement.getBoundingClientRect();
    const bx = rect ? rect.left : 0;
    const by = rect ? rect.top : 0;
    return {
      x: (clientX - bx - this.panX) / this.scale,
      y: (clientY - by - this.panY) / this.scale,
    };
  }

  private clampScale(s: number): number {
    return Math.min(this.MAX_SCALE, Math.max(this.MIN_SCALE, s));
  }

  // Zoom mantendo fixo o ponto sob o cursor (em px relativos ao canvas).
  private zoomAt(px: number, py: number, factor: number): void {
    const newScale = this.clampScale(this.scale * factor);
    if (newScale === this.scale) return;
    const k = newScale / this.scale;
    this.panX = px - k * (px - this.panX);
    this.panY = py - k * (py - this.panY);
    this.scale = newScale;
  }

  onCanvasWheel(ev: WheelEvent): void {
    ev.preventDefault();
    const rect = this.canvasRef?.nativeElement.getBoundingClientRect();
    const px = ev.clientX - (rect ? rect.left : 0);
    const py = ev.clientY - (rect ? rect.top : 0);
    this.zoomAt(px, py, ev.deltaY < 0 ? 1.12 : 1 / 1.12);
  }

  // Zoom pelos botões: mantém o centro do canvas fixo.
  zoomBy(factor: number): void {
    const rect = this.canvasRef?.nativeElement.getBoundingClientRect();
    const px = rect ? rect.width / 2 : 0;
    const py = rect ? rect.height / 2 : 0;
    this.zoomAt(px, py, factor);
  }

  // Ajusta o zoom/pan para enquadrar todos os nós no viewport.
  fitView(): void {
    const rect = this.canvasRef?.nativeElement.getBoundingClientRect();
    if (!rect || !this.nodes.length) {
      this.scale = 1;
      this.panX = 0;
      this.panY = 0;
      return;
    }
    const NODE_W = 180;
    const NODE_H = 70;
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const n of this.nodes) {
      minX = Math.min(minX, n.canvas.x);
      minY = Math.min(minY, n.canvas.y);
      maxX = Math.max(maxX, n.canvas.x + NODE_W);
      maxY = Math.max(maxY, n.canvas.y + NODE_H);
    }
    const pad = 60;
    const contentW = maxX - minX + pad * 2;
    const contentH = maxY - minY + pad * 2;
    const scale = this.clampScale(
      Math.min(rect.width / contentW, rect.height / contentH),
    );
    this.scale = scale;
    // Centraliza o bounding box.
    this.panX = (rect.width - (maxX - minX) * scale) / 2 - minX * scale;
    this.panY = (rect.height - (maxY - minY) * scale) / 2 - minY * scale;
  }

  // ── Ligações (arestas) ─────────────────────────────────────────────
  startLink(ev: MouseEvent, n: PlaybookNode): void {
    ev.stopPropagation();
    this.linkingFrom = n.id!;
  }

  private completeLink(target: PlaybookNode): void {
    const src = this.linkingFrom;
    this.linkingFrom = null;
    if (!src || src === target.id) return;
    const exists = this.edges.some((e) => e.source === src && e.target === target.id);
    if (!exists) this.edges.push({ source: src, target: target.id! });
  }

  removeEdge(e: PlaybookEdge): void {
    this.edges = this.edges.filter((x) => !(x.source === e.source && x.target === e.target));
  }

  // Coordenadas do centro de um nó (para desenhar as arestas). Números
  // aproximam o tamanho do card (largura 180, altura ~70).
  nodeCenter(id: string): { x: number; y: number } {
    const n = this.nodes.find((x) => x.id === id);
    if (!n) return { x: 0, y: 0 };
    return { x: n.canvas.x + 90, y: n.canvas.y + 35 };
  }

  edgePath(e: PlaybookEdge): string {
    const NODE_W = 180;
    const HALF_H = 35;
    const a = this.nodeCenter(e.source);
    const b = this.nodeCenter(e.target);
    // Saída pela direita da origem, chegada pela esquerda do destino, para
    // que a ponta da seta fique visível na borda do card (não sob ele).
    const src = this.nodes.find((n) => n.id === e.source);
    const tgt = this.nodes.find((n) => n.id === e.target);
    let ax = a.x, ay = a.y, bx = b.x, by = b.y;
    if (tgt) {
      // Entra pela esquerda do destino se a origem estiver à esquerda, senão pela direita.
      if (a.x <= tgt.canvas.x + NODE_W / 2) {
        bx = tgt.canvas.x - 4;
        by = tgt.canvas.y + HALF_H;
      } else {
        bx = tgt.canvas.x + NODE_W + 4;
        by = tgt.canvas.y + HALF_H;
      }
    }
    if (src) {
      ax = bx >= a.x ? src.canvas.x + NODE_W : src.canvas.x;
      ay = src.canvas.y + HALF_H;
    }
    // Curva de Bézier horizontal simples.
    const mx = (ax + bx) / 2;
    return `M ${ax} ${ay} C ${mx} ${ay}, ${mx} ${by}, ${bx} ${by}`;
  }

  edgeMid(e: PlaybookEdge): { x: number; y: number } {
    const a = this.nodeCenter(e.source);
    const b = this.nodeCenter(e.target);
    return { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
  }

  nodeName(id: string): string {
    return this.nodes.find((n) => n.id === id)?.name || id;
  }

  // ── Tools do nó selecionado ────────────────────────────────────────
  isToolOn(n: PlaybookNode, slug: string): boolean {
    return n.tools_enabled.includes(slug);
  }

  toggleTool(n: PlaybookNode, slug: string): void {
    const i = n.tools_enabled.indexOf(slug);
    if (i >= 0) n.tools_enabled.splice(i, 1);
    else n.tools_enabled.push(slug);
  }

  // ── Sugestões ──────────────────────────────────────────────────────
  addSuggestion(): void {
    this.suggestions.push({ title: '', text: '' });
  }

  removeSuggestion(i: number): void {
    this.suggestions.splice(i, 1);
  }

  // ── Salvar ─────────────────────────────────────────────────────────
  save(): void {
    if (!this.name.trim()) {
      this.tab = 'canvas';
      this.status = '❌ Dê um nome ao playbook.';
      return;
    }
    if (this.nodes.filter((n) => n.is_root).length !== 1) {
      this.status = '❌ Marque exatamente um nó como root.';
      return;
    }
    this.saving = true;
    this.status = '⏳ Salvando…';
    this.warnings = [];
    const payload = {
      name: this.name.trim(),
      description: this.description.trim(),
      icon: this.icon.trim() || '📘',
      // Mantém o id de cliente (para o backend casar as arestas) mas NÃO o usa
      // como slug: nó novo vai com slug vazio e o backend o deriva do nome
      // (evita slugs feios tipo 'n2_41530' virando o "nome" do agente).
      nodes: this.nodes.map((n) => ({ ...n, slug: n.slug || '' })),
      edges: this.edges.map((e) => ({ ...e })),
      suggestions: this.suggestions
        .map((s) => ({ title: (s.title || '').trim(), text: (s.text || '').trim() }))
        .filter((s) => s.text),
    };
    const req = this.playbookId
      ? this.api.update(this.playbookId, payload)
      : this.api.create(payload);
    req.subscribe({
      next: (data) => {
        this.saving = false;
        if (data.status === 'success') {
          this.warnings = data.warnings || [];
          this.status = this.warnings.length ? '✅ Salvo (com avisos).' : '✅ Salvo!';
          // Adota o id e o grafo canônico devolvido pelo backend. Sem isto, um
          // segundo "Salvar" de um playbook novo cairia em create() de novo e
          // criaria uma duplicata; além disso realinha os slugs de cliente.
          if (data.playbook) this.applyDetail(data.playbook);
          this.saved.emit();
        } else {
          this.status = '❌ ' + (data.message || 'Erro ao salvar.');
        }
      },
      error: (e) => {
        this.saving = false;
        this.status = '❌ ' + (e?.error?.message || e?.message || 'Erro ao salvar.');
      },
    });
  }

  // ── Exportar / Importar JSON ───────────────────────────────────────
  // Formato de arquivo portável: o grafo do playbook sem o id do registro nem
  // os ids de cliente dos nós (que são efêmeros). Um envelope com versão
  // permite evoluir o formato no futuro.
  private readonly EXPORT_VERSION = 1;

  exportJson(): void {
    const doc = {
      _type: 'playbook',
      _version: this.EXPORT_VERSION,
      name: this.name.trim(),
      description: this.description.trim(),
      icon: this.icon.trim() || '📘',
      // Slug estável, sem o id de cliente (reconstruído no import).
      nodes: this.nodes.map(({ id, ...n }) => ({ ...n, slug: n.slug || id || '' })),
      edges: this.edges.map((e) => ({ ...e })),
      suggestions: this.suggestions.map((s) => ({ ...s })),
    };
    const json = JSON.stringify(doc, null, 2);
    const blob = new Blob([json], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${this.slugForFile(this.name) || 'playbook'}.json`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    this.status = '⬇️ JSON exportado.';
  }

  // Nome de arquivo seguro a partir do nome do playbook.
  private slugForFile(name: string): string {
    return (name || '')
      .trim()
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '')
      .slice(0, 60);
  }

  // Abre o seletor de arquivo (o input está escondido no template).
  triggerImport(): void {
    this.fileInputRef?.nativeElement.click();
  }

  onImportFile(ev: Event): void {
    const input = ev.target as HTMLInputElement;
    const file = input.files && input.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      try {
        this.applyImported(JSON.parse(String(reader.result || '')));
      } catch {
        this.status = '❌ Arquivo inválido: não é um JSON de playbook.';
      }
      // Permite reimportar o mesmo arquivo (o change não dispara sem reset).
      input.value = '';
    };
    reader.onerror = () => {
      this.status = '❌ Não foi possível ler o arquivo.';
      input.value = '';
    };
    reader.readAsText(file);
  }

  // Carrega um documento importado no editor SEM salvar: vira um playbook novo
  // (id zerado) para não sobrescrever outro por engano — o usuário revisa e
  // clica em Salvar. Aceita tanto o envelope exportado quanto um PlaybookDetail
  // cru (nodes/edges/suggestions).
  private applyImported(doc: any): void {
    if (!doc || typeof doc !== 'object' || !Array.isArray(doc.nodes) || !doc.nodes.length) {
      this.status = '❌ Arquivo inválido: sem nós de playbook.';
      return;
    }
    const nodes: PlaybookNode[] = doc.nodes.map((n: any, i: number) => {
      const slug = (n?.slug || '').toString() || `no_${i + 1}`;
      return {
        slug,
        id: slug,
        name: (n?.name || '').toString(),
        description: (n?.description || '').toString(),
        icon: (n?.icon || '🤖').toString(),
        system_prompt: (n?.system_prompt || '').toString(),
        // Modelo/tools inválidos são saneados no backend ao salvar.
        model: (n?.model || this.models[0] || 'gpt-4o').toString(),
        temperature: typeof n?.temperature === 'number' ? n.temperature : 0.7,
        tools_enabled: Array.isArray(n?.tools_enabled)
          ? n.tools_enabled.map((s: any) => String(s))
          : [],
        is_root: !!n?.is_root,
        canvas: {
          x: Number(n?.canvas?.x) || 0,
          y: Number(n?.canvas?.y) || 0,
        },
      };
    });
    const validSlugs = new Set(nodes.map((n) => n.slug));
    const edges: PlaybookEdge[] = (Array.isArray(doc.edges) ? doc.edges : [])
      .filter(
        (e: any) =>
          e && validSlugs.has(String(e.source)) && validSlugs.has(String(e.target)),
      )
      .map((e: any) => ({ source: String(e.source), target: String(e.target) }));
    const suggestions: PlaybookSuggestion[] = (Array.isArray(doc.suggestions) ? doc.suggestions : [])
      .filter((s: any) => s && (s.title || s.text))
      .map((s: any) => ({ title: (s.title || '').toString(), text: (s.text || '').toString() }));

    // Importar cria um playbook NOVO (não sobrescreve o aberto).
    this.playbookId = null;
    this.name = (doc.name || '').toString();
    this.description = (doc.description || '').toString();
    this.icon = (doc.icon || '📘').toString() || '📘';
    this.nodes = nodes;
    this.edges = edges;
    this.suggestions = suggestions;
    this.selectedId = nodes.find((n) => n.is_root)?.id ?? nodes[0].id ?? null;
    this.tab = 'canvas';
    this.warnings = [];
    this.fitView();
    this.status = '📥 Playbook importado — revise e clique em Salvar para gravar.';
  }

  close(): void {
    this.closed.emit();
  }

  // Arestas que saem do nó selecionado (mostradas no painel).
  outgoingOf(id: string): PlaybookEdge[] {
    return this.edges.filter((e) => e.source === id);
  }

  trackNode = (_: number, n: PlaybookNode) => n.id;
  trackEdge = (_: number, e: PlaybookEdge) => `${e.source}->${e.target}`;
  trackTool = (_: number, t: ToolInfo) => t.slug;
}
