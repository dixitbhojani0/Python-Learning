import { Injectable } from '@angular/core';
import { Router } from '@angular/router';
import { UserSession } from '../models/api.models';
import { environment } from '../../../environments/environment';

@Injectable({ providedIn: 'root' })
export class AuthService {
  private readonly KEY = 'sdlc_session';

  constructor(private router: Router) {}

  login(session: UserSession): void {
    localStorage.setItem(this.KEY, JSON.stringify(session));
  }

  logout(): void {
    localStorage.removeItem(this.KEY);
    this.router.navigate(['/login']);
  }

  getSession(): UserSession | null {
    const raw = localStorage.getItem(this.KEY);
    if (!raw) return null;
    const session = JSON.parse(raw) as UserSession;
    // Self-heal: if project was saved under a different name (e.g. old 'antlog' session),
    // update it to the current environment value and re-persist so all API calls use it.
    if (session.project !== environment.defaultProject) {
      session.project = environment.defaultProject;
      localStorage.setItem(this.KEY, JSON.stringify(session));
    }
    return session;
  }

  getToken(): string {
    return this.getSession()?.token ?? '';
  }

  isLoggedIn(): boolean {
    return !!this.getSession();
  }

  // manager, technical_leader, and admin can access the admin panel
  isAdmin(): boolean {
    const role = this.getSession()?.role;
    return role === 'manager' || role === 'technical_leader' || role === 'admin';
  }
}
