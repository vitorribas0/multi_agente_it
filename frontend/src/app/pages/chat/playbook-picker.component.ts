import {
  Component,
  EventEmitter,
  HostListener,
  Input,
  OnChanges,
  Output,
  SimpleChanges,
} from '@angular/core';
import { CommonModule } from '@angular/common';

import { PlaybookService } from '../../api/playbook.service';
import { PlaybookSummary } from '../../api/playbook.models';

// Modal "Playbook": lista os playbooks cadastrados e deixa o usuário escolher
// um para a conversa (isolamento total) ou "Nenhum" para voltar aos agentes
// globais. Não persiste sozinho — emite (select) com o playbook (ou null);
// quem grava o vínculo (bindConversation / payload do stream) é o chat-page.
@Component({
  selector: 'app-playbook-picker',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './playbook-picker.component.html',
})
export class PlaybookPickerComponent implements OnChanges {
  @Input() open = false;
  // Playbook ativo na conversa (para marcar o selecionado).
  @Input() activeId: number | null = null;

  @Output() select = new EventEmitter<PlaybookSummary | null>();
  @Output() closed = new EventEmitter<void>();

  playbooks: PlaybookSummary[] = [];
  loading = false;
  loaded = false;

  constructor(private api: PlaybookService) {}

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['open'] && this.open) this.onOpen();
  }

  private onOpen(): void {
    document.body.classList.add('modal-open');
    this.fetch();
  }

  private fetch(): void {
    this.loading = true;
    this.api.list().subscribe({
      next: (data) => {
        this.playbooks = data.playbooks || [];
        this.loaded = true;
        this.loading = false;
      },
      error: () => {
        this.loading = false;
      },
    });
  }

  isActive(id: number | null): boolean {
    return this.activeId === id;
  }

  choose(pb: PlaybookSummary | null): void {
    this.select.emit(pb);
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

  trackPb = (_: number, p: PlaybookSummary) => p.id;
}
