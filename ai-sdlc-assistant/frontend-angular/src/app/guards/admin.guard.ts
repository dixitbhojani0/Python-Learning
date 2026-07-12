import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';
import { AuthService } from '../core/services/auth.service';

// Protects /admin — only manager, technical_leader, and admin roles can enter.
export const adminGuard: CanActivateFn = () => {
  const auth = inject(AuthService);
  if (auth.isAdmin()) return true;
  inject(Router).navigate(['/chat']);
  return false;
};
