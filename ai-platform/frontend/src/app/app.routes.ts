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
  // Admin is 5 separate routes, not one page — mirrors the reference control
  // panel's per-concern navigation instead of a single scrolling stack of
  // cards. No client-side permission gating on any of them (§P) — the JWT's
  // permissions aren't decoded on the frontend today, only enforced
  // server-side per endpoint (admin_routes.py). A non-admin can open any of
  // these but sees per-section "accessDenied" text instead of data, never a
  // broken page.
  { path: 'admin', pathMatch: 'full', redirectTo: 'admin/overview' },
  {
    path: 'admin/overview',
    loadComponent: () => import('./features/admin/overview/overview').then((m) => m.AdminOverviewComponent),
    canActivate: [authGuard],
  },
  {
    path: 'admin/documents',
    loadComponent: () => import('./features/admin/documents/documents').then((m) => m.AdminDocumentsComponent),
    canActivate: [authGuard],
  },
  {
    path: 'admin/documents/:id',
    loadComponent: () =>
      import('./features/admin/documents/document-detail').then((m) => m.AdminDocumentDetailComponent),
    canActivate: [authGuard],
  },
  {
    path: 'admin/team',
    loadComponent: () => import('./features/admin/team/team').then((m) => m.AdminTeamComponent),
    canActivate: [authGuard],
  },
  {
    path: 'admin/approvals',
    loadComponent: () => import('./features/admin/approvals/approvals').then((m) => m.AdminApprovalsComponent),
    canActivate: [authGuard],
  },
  {
    path: 'admin/audit',
    loadComponent: () => import('./features/admin/audit/audit').then((m) => m.AdminAuditComponent),
    canActivate: [authGuard],
  },
  { path: '', pathMatch: 'full', redirectTo: 'chat' },
  { path: '**', redirectTo: 'chat' },
];
