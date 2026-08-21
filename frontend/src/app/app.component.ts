import { Component } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';

import { HistoryListComponent } from './shared/history-list.component';

// Shell da aplicação: espelha o layout do base.html (sidebar + main).
// A sidebar reúne navegação (Chat/Manual/Configurações) + histórico de
// conversas (app-history-list). O Manual ainda é placeholder até ser migrado.
@Component({
  selector: 'app-root',
  standalone: true,
  imports: [CommonModule, RouterOutlet, RouterLink, RouterLinkActive, HistoryListComponent],
  templateUrl: './app.component.html',
})
export class AppComponent {
  sidebarCollapsed = false;

  constructor() {
    this.sidebarCollapsed = localStorage.getItem('sidebar-collapsed') === '1';
  }

  toggleSidebar(): void {
    this.sidebarCollapsed = !this.sidebarCollapsed;
    localStorage.setItem('sidebar-collapsed', this.sidebarCollapsed ? '1' : '0');
  }
}
