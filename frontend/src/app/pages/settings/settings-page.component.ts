import { Component, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';

import { ConfigService } from '../../api/config.service';
import {
  AgentInfo,
  ConfigOverview,
  Knowledge,
  Skill,
  ToolInfo,
} from '../../api/config.models';

type TabKey = 'general' | 'agents' | 'knowledge' | 'skills' | 'tools';
type StatusKind = '' | 'success' | 'error';

interface SaveStatus {
  text: string;
  kind: StatusKind;
}

// Estado de edição local de um agente (o form é controlado pelo Angular,
// não lemos do DOM como o settings.js fazia).
interface AgentEdit extends AgentInfo {
  _status: SaveStatus;
  _saving: boolean;
}

// Card de conhecimento em edição. id === null => card de criação novo.
interface KnowledgeEdit {
  id: number | null;
  icon: string;
  name: string;
  description: string;
  prompt: string;
  _status: SaveStatus;
  _saving: boolean;
}

interface SkillEdit extends Skill {
  _new: boolean;
  _status: SaveStatus;
  _saving: boolean;
}

@Component({
  selector: 'app-settings-page',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './settings-page.component.html',
  // O elemento-host do componente fica ENTRE .main e .content-page. Sem isto
  // ele não participa da cadeia flex e o .content-page perde a altura de
  // referência — resultado: o scroll interno não funciona. Fazemos o host
  // preencher o .main como coluna flex para o .content-page voltar a rolar.
  styles: [`
    :host {
      flex: 1;
      display: flex;
      flex-direction: column;
      min-height: 0;
    }
  `],
})
export class SettingsPageComponent implements OnInit {
  activeTab: TabKey = 'general';

  loading = true;
  models: string[] = [];
  tools: ToolInfo[] = [];
  agents: AgentEdit[] = [];

  // Geral
  maxIterations = 18;
  generalStatus: SaveStatus = { text: '', kind: '' };
  savingGeneral = false;

  // Análise massiva
  massivaWorkers = 5;
  massivaStatus: SaveStatus = { text: '', kind: '' };
  savingMassiva = false;

  // Conhecimentos
  knowledgeLoading = true;
  knowledgeCards: KnowledgeEdit[] = [];

  // Skills: arquivos reais SKILL.md usados pela Atena.
  skillsLoading = true;
  skillCards: SkillEdit[] = [];

  constructor(private api: ConfigService) {}

  ngOnInit(): void {
    this.loadConfig();
    this.loadKnowledge();
    this.loadSkills();
  }

  setTab(tab: TabKey): void {
    this.activeTab = tab;
  }

  // ── Carga ────────────────────────────────────────────────────────
  private loadConfig(): void {
    this.loading = true;
    this.api.getConfig().subscribe({
      next: (cfg: ConfigOverview) => {
        this.models = cfg.models || [];
        this.tools = cfg.tools || [];
        this.agents = (cfg.agents || []).map((a) => ({
          ...a,
          tools_enabled: [...(a.tools_enabled || [])],
          _status: { text: '', kind: '' },
          _saving: false,
        }));
        const s = cfg.settings || { max_iterations: 18, massiva_workers: 5 };
        this.maxIterations = s.max_iterations ?? 18;
        this.massivaWorkers = s.massiva_workers ?? 5;
        this.loading = false;
      },
      error: (e) => {
        console.error('Erro ao carregar config:', e);
        this.loading = false;
      },
    });
  }

  // ── Geral ────────────────────────────────────────────────────────
  saveGeneral(): void {
    let n = Number.parseInt(String(this.maxIterations), 10);
    if (!Number.isFinite(n)) n = 18;
    n = Math.max(1, Math.min(100, n));
    this.maxIterations = n;

    this.savingGeneral = true;
    this.generalStatus = { text: '⏳ Salvando…', kind: '' };
    this.api.saveSettings({ max_iterations: n }).subscribe({
      next: (data) => {
        if (data.status === 'success') {
          if (data.settings) this.maxIterations = data.settings.max_iterations;
          this.flash('general', '✅ Salvo!', 'success');
        } else {
          this.flash('general', '❌ ' + (data.message || 'Erro ao salvar'), 'error');
        }
        this.savingGeneral = false;
      },
      error: (e) => {
        this.flash('general', '❌ ' + this.httpErr(e), 'error');
        this.savingGeneral = false;
      },
    });
  }

  // ── Análise massiva ──────────────────────────────────────────────
  get massivaWarn(): { cls: string; ico: string; msg: string } | null {
    const n = this.massivaWorkers;
    if (n >= 10) {
      return {
        cls: 'is-danger',
        ico: '🛑',
        msg: 'Máximo (10). Alto risco de estourar o limite de requisições do provedor (rate limit) e concentrar custo. Use apenas em cargas controladas e por curtos períodos.',
      };
    }
    if (n >= 6) {
      return {
        cls: 'is-warn',
        ico: '⚠️',
        msg: 'Acima do padrão. Acelera, mas aumenta a chance de rate limit conforme o provedor/modelo. Monitore os erros no resultado da análise.',
      };
    }
    return null;
  }

  clampMassiva(): void {
    let v = Number.parseInt(String(this.massivaWorkers), 10);
    if (!Number.isFinite(v)) v = 5;
    this.massivaWorkers = Math.max(1, Math.min(10, v));
  }

  saveMassiva(): void {
    this.clampMassiva();
    const n = this.massivaWorkers;
    if (n >= 10 && !confirm('Definir 10 workers é o máximo e pode causar rate limit no provedor. Confirmar?')) {
      return;
    }
    this.savingMassiva = true;
    this.massivaStatus = { text: '⏳ Salvando…', kind: '' };
    this.api.saveSettings({ massiva_workers: n }).subscribe({
      next: (data) => {
        if (data.status === 'success') {
          if (data.settings) this.massivaWorkers = data.settings.massiva_workers;
          this.flash('massiva', '✅ Salvo!', 'success');
        } else {
          this.flash('massiva', '❌ ' + (data.message || 'Erro ao salvar'), 'error');
        }
        this.savingMassiva = false;
      },
      error: (e) => {
        this.flash('massiva', '❌ ' + this.httpErr(e), 'error');
        this.savingMassiva = false;
      },
    });
  }

  // Extrai a mensagem REAL do erro HTTP. O backend responde
  // {status:'error', message:'…'}; sem isto o front mostrava só o
  // e.message genérico ("Http failure response…") e escondia a causa.
  private httpErr(e: any): string {
    return (
      e?.error?.message ||
      e?.error?.detail ||
      (typeof e?.error === 'string' ? e.error : '') ||
      e?.message ||
      'Erro ao salvar'
    );
  }

  // ── Agentes ──────────────────────────────────────────────────────
  toolsOnCount(agent: AgentEdit): number {
    return this.tools.filter((t) => agent.tools_enabled.includes(t.slug)).length;
  }

  isToolOn(agent: AgentEdit, slug: string): boolean {
    return agent.tools_enabled.includes(slug);
  }

  toggleTool(agent: AgentEdit, slug: string): void {
    const i = agent.tools_enabled.indexOf(slug);
    if (i >= 0) agent.tools_enabled.splice(i, 1);
    else agent.tools_enabled.push(slug);
  }

  bulkTools(agent: AgentEdit, all: boolean): void {
    agent.tools_enabled = all ? this.tools.map((t) => t.slug) : [];
  }

  saveAgent(agent: AgentEdit): void {
    agent._saving = true;
    agent._status = { text: '⏳ Salvando…', kind: '' };
    this.api
      .saveAgent(agent.slug, {
        model: agent.model,
        temperature: Number(agent.temperature),
        system_prompt: agent.system_prompt,
        tools_enabled: [...agent.tools_enabled],
      })
      .subscribe({
        next: (data) => {
          if (data.status === 'success') {
            agent._status = { text: '✅ Salvo!', kind: 'success' };
          } else {
            agent._status = { text: '❌ ' + (data.message || 'Erro ao salvar'), kind: 'error' };
          }
          agent._saving = false;
          this.clearStatusLater(() => (agent._status = { text: '', kind: '' }));
        },
        error: (e) => {
          agent._status = { text: '❌ ' + e.message, kind: 'error' };
          agent._saving = false;
          this.clearStatusLater(() => (agent._status = { text: '', kind: '' }));
        },
      });
  }

  // ── Conhecimentos ────────────────────────────────────────────────
  private loadKnowledge(): void {
    this.knowledgeLoading = true;
    this.api.listKnowledge().subscribe({
      next: (data) => {
        this.knowledgeCards = (data?.knowledge || []).map((k) => this.toEdit(k));
        this.knowledgeLoading = false;
      },
      error: () => {
        this.knowledgeCards = [];
        this.knowledgeLoading = false;
      },
    });
  }

  private toEdit(k: Knowledge): KnowledgeEdit {
    return {
      id: k.id,
      icon: k.icon || '📚',
      name: k.name,
      description: k.description || '',
      prompt: k.prompt,
      _status: { text: '', kind: '' },
      _saving: false,
    };
  }

  // ── Skills ──────────────────────────────────────────────────────
  private loadSkills(): void {
    this.skillsLoading = true;
    this.api.listSkills().subscribe({
      next: (data) => {
        this.skillCards = (data.skills || []).map((skill) => this.toSkillEdit(skill));
        this.skillsLoading = false;
      },
      error: () => {
        this.skillCards = [];
        this.skillsLoading = false;
      },
    });
  }

  private toSkillEdit(skill: Skill): SkillEdit {
    return { ...skill, _new: false, _status: { text: '', kind: '' }, _saving: false };
  }

  addSkillCard(): void {
    if (this.skillCards.some((skill) => skill._new)) return;
    this.skillCards.unshift({
      slug: '', name: '', description: '',
      prompt: '---\nname: minha-skill\ndescription: ""\n---\n\n# Minha skill\n\nDescreva aqui as instruções que a Atena deve seguir.\n',
      _new: true, _status: { text: '', kind: '' }, _saving: false,
    });
  }

  saveSkill(skill: SkillEdit): void {
    const payload = {
      slug: skill.slug.trim(), name: skill.name.trim(), description: skill.description.trim(), prompt: skill.prompt.trim(),
    };
    if (!payload.name || !payload.prompt) {
      skill._status = { text: '❌ Nome e prompt são obrigatórios', kind: 'error' };
      return;
    }
    skill._saving = true;
    skill._status = { text: '⏳ Salvando…', kind: '' };
    this.api.saveSkill(payload).subscribe({
      next: (data) => {
        if (data.status === 'success') this.loadSkills();
        else { skill._status = { text: '❌ ' + (data.message || 'Erro ao salvar'), kind: 'error' }; skill._saving = false; }
      },
      error: (e) => { skill._status = { text: '❌ ' + this.httpErr(e), kind: 'error' }; skill._saving = false; },
    });
  }

  deleteSkill(skill: SkillEdit): void {
    if (skill._new) { this.skillCards = this.skillCards.filter((item) => item !== skill); return; }
    if (!confirm(`Excluir a skill "${skill.name}"? O arquivo SKILL.md será removido.`)) return;
    skill._saving = true;
    this.api.deleteSkill(skill.slug).subscribe({
      next: (data) => data.status === 'success' ? this.loadSkills() : skill._status = { text: '❌ ' + (data.message || 'Erro ao excluir'), kind: 'error' },
      error: (e) => { skill._status = { text: '❌ ' + this.httpErr(e), kind: 'error' }; skill._saving = false; },
    });
  }

  addKnowledgeCard(): void {
    // Evita abrir dois formulários de criação simultâneos.
    if (this.knowledgeCards.some((c) => c.id === null)) return;
    this.knowledgeCards.unshift({
      id: null,
      icon: '📚',
      name: '',
      description: '',
      prompt: '',
      _status: { text: '', kind: '' },
      _saving: false,
    });
  }

  saveKnowledge(card: KnowledgeEdit): void {
    const payload = {
      icon: (card.icon || '📚').trim() || '📚',
      name: card.name.trim(),
      description: card.description.trim(),
      prompt: card.prompt.trim(),
    };
    if (!payload.name) {
      card._status = { text: '❌ Nome é obrigatório', kind: 'error' };
      return;
    }
    if (!payload.prompt) {
      card._status = { text: '❌ Prompt é obrigatório', kind: 'error' };
      return;
    }

    card._saving = true;
    card._status = { text: '⏳ Salvando…', kind: '' };
    const req = card.id
      ? this.api.updateKnowledge(card.id, payload)
      : this.api.createKnowledge(payload);

    req.subscribe({
      next: (data) => {
        if (data.status === 'success') {
          card._status = { text: '✅ Salvo!', kind: 'success' };
          this.loadKnowledge();
        } else {
          card._status = { text: '❌ ' + (data.message || 'Erro ao salvar'), kind: 'error' };
          card._saving = false;
        }
      },
      error: (e) => {
        card._status = { text: '❌ ' + e.message, kind: 'error' };
        card._saving = false;
      },
    });
  }

  deleteKnowledge(card: KnowledgeEdit): void {
    if (card.id === null) {
      // Card de criação ainda não salvo: só remove da tela.
      this.knowledgeCards = this.knowledgeCards.filter((c) => c !== card);
      return;
    }
    if (!confirm(`Excluir o conhecimento "${card.name}"? Esta ação não pode ser desfeita.`)) return;
    card._status = { text: '⏳ Excluindo…', kind: '' };
    this.api.deleteKnowledge(card.id).subscribe({
      next: (data) => {
        if (data.status === 'success') {
          this.loadKnowledge();
        } else {
          card._status = { text: '❌ ' + (data.message || 'Erro ao excluir'), kind: 'error' };
        }
      },
      error: (e) => {
        card._status = { text: '❌ ' + e.message, kind: 'error' };
      },
    });
  }

  // ── Helpers de status ────────────────────────────────────────────
  private flash(which: 'general' | 'massiva', text: string, kind: StatusKind): void {
    const status: SaveStatus = { text, kind };
    if (which === 'general') this.generalStatus = status;
    else this.massivaStatus = status;
    this.clearStatusLater(() => {
      if (which === 'general') this.generalStatus = { text: '', kind: '' };
      else this.massivaStatus = { text: '', kind: '' };
    });
  }

  private clearStatusLater(fn: () => void): void {
    setTimeout(fn, 3000);
  }

  // trackBy para não recriar cards ao re-renderizar.
  trackAgent = (_: number, a: AgentEdit) => a.slug;
  trackTool = (_: number, t: ToolInfo) => t.slug;
  trackKnowledge = (_: number, k: KnowledgeEdit) => (k.id === null ? 'new' : k.id);
  trackSkill = (_: number, skill: SkillEdit) => (skill._new ? 'new' : skill.slug);

  paramEntries(t: ToolInfo): Array<{ key: string; type: string; required: boolean }> {
    return Object.entries(t.parameters || {}).map(([key, meta]) => ({
      key,
      type: (meta?.type as string) || 'any',
      required: (t.required || []).includes(key),
    }));
  }
}
