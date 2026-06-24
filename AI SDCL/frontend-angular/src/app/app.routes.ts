import { Routes } from '@angular/router';
import { authGuard } from './guards/auth.guard';
import { adminGuard } from './guards/admin.guard';

export const routes: Routes = [
  { path: '', redirectTo: 'login', pathMatch: 'full' },

  {
    path: 'login',
    loadComponent: () => import('./login/login').then(m => m.Login),
  },
  {
    path: 'chat',
    loadComponent: () => import('./chat/chat').then(m => m.Chat),
    canActivate: [authGuard],
  },
  {
    path: 'admin',
    loadComponent: () => import('./admin/admin').then(m => m.Admin),
    canActivate: [authGuard, adminGuard],
    children: [
      { path: '', redirectTo: 'stats', pathMatch: 'full' },
      { path: 'stats',    loadComponent: () => import('./admin/stats/stats').then(m => m.Stats) },
      { path: 'rag',      loadComponent: () => import('./admin/rag-manager/rag-manager').then(m => m.RagManager) },
      { path: 'memory',   loadComponent: () => import('./admin/memory/memory').then(m => m.MemoryComponent) },
      { path: 'config',   loadComponent: () => import('./admin/config-viewer/config-viewer').then(m => m.ConfigViewer) },
      { path: 'sessions', loadComponent: () => import('./admin/sessions/sessions').then(m => m.Sessions) },
    ],
  },
  { path: '**', redirectTo: 'login' },
];
