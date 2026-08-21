import { Component, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';

import { PlaybookService } from '../../api/playbook.service';
import { PlaybookSummary } from '../../api/playbook.models';
import { PlaybookEditorComponent } from './playbook-editor.component';

// Tela Playbooks: lista os pipelines cadastrados (cards) e hospeda o editor
// de canvas. `editing` guarda o estado da navegação lista ↔ editor:
//   null           -> lista
//   { id: number } -> editando um playbook existente
//   { id: null }   -> criando um novo
@Component({
  selector: 'app-playbooks-page',
  standalone: true,
  imports: [CommonModule, PlaybookEditorComponent],
  templateUrl: './playbooks-page.component.html',
  styleUrls: ['./playbooks-page.component.css'],
  styles: [`
    :host {
      flex: 1;
      display: flex;
      flex-direction: column;
      min-height: 0;
    }
  `],
})
export class PlaybooksPageComponent implements OnInit {
  playbooks: PlaybookSummary[] = [];
  loading = true;
  editing: { id: number | null } | null = null;

  constructor(private api: PlaybookService) {}

  ngOnInit(): void {
    this.load();
  }

  private load(): void {
    this.loading = true;
    this.api.list().subscribe({
      next: (data) => {
        this.playbooks = data.playbooks || [];
        this.loading = false;
      },
      error: () => {
        this.playbooks = [];
        this.loading = false;
      },
    });
  }

  createNew(): void {
    this.editing = { id: null };
  }

  edit(pb: PlaybookSummary): void {
    this.editing = { id: pb.id };
  }

  onEditorClosed(): void {
    this.editing = null;
    this.load();
  }

  onEditorSaved(): void {
    // O editor emite após salvar; recarrega a lista mas mantém o editor aberto
    // (o usuário pode continuar ajustando). A lista atualiza ao voltar.
    this.load();
  }

  remove(pb: PlaybookSummary): void {
    if (!confirm(`Excluir o playbook "${pb.name}"? Conversas vinculadas voltam a usar os agentes globais.`)) {
      return;
    }
    this.api.delete(pb.id).subscribe({
      next: () => this.load(),
      error: () => this.load(),
    });
  }

  trackPb = (_: number, p: PlaybookSummary) => p.id;
}
