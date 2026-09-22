import { Routes } from '@angular/router';
import { authGuard } from './core/auth/auth.guard';

export const routes: Routes = [
  {
    path: 'health',
    loadComponent: () => import('./features/health/health').then((m) => m.HealthComponent),
  },
  {
    path: 'login',
    loadComponent: () => import('./features/login/login').then((m) => m.LoginComponent),
  },
  {
    path: 'chat',
    loadComponent: () => import('./features/chat/chat').then((m) => m.ChatComponent),
    canActivate: [authGuard],
  },
  {
    path: 'admin',
    loadComponent: () => import('./features/admin/admin').then((m) => m.AdminComponent),
    // No client-side permission gating (§P) — the JWT's permissions aren't
    // decoded on the frontend today, only enforced server-side per endpoint
    // (admin_routes.py). A non-admin can open this route but sees per-section
    // "accessDenied" text instead of data, never a broken page.
    canActivate: [authGuard],
  },
  { path: '', pathMatch: 'full', redirectTo: 'chat' },
  { path: '**', redirectTo: 'chat' },
];
