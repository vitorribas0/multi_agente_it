import { Routes } from '@angular/router';
import { SettingsPageComponent } from './pages/settings/settings-page.component';
import { ChatPageComponent } from './pages/chat/chat-page.component';
import { PlaybooksPageComponent } from './pages/playbooks/playbooks-page.component';

export const routes: Routes = [
  // Chat (núcleo: mensagem + streaming SSE + histórico + markdown) e
  // Configurações migrados.
  { path: 'chat', component: ChatPageComponent },
  { path: 'playbooks', component: PlaybooksPageComponent },
  { path: 'settings', component: SettingsPageComponent },
  { path: '', pathMatch: 'full', redirectTo: 'chat' },
  { path: '**', redirectTo: 'chat' },
];
