import { Component } from '@angular/core';
import { Router } from '@angular/router';
import { CommonModule } from '@angular/common';
import { MatCardModule } from '@angular/material/card';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { environment } from '../../environments/environment';
import { AuthService } from '../core/services/auth.service';
import { UserSession } from '../core/models/api.models';

@Component({
  selector: 'app-login',
  standalone: true,
  imports: [CommonModule, MatCardModule, MatButtonModule, MatIconModule],
  templateUrl: './login.html',
  styleUrl: './login.css',
})
export class Login {
  // Demo tokens from backend/auth/middleware.py — matches DEMO_TOKEN_* in .env
  roles: (UserSession & { icon: string; description: string })[] = [
    {
      token: 'dev_token_alice',
      role: 'developer',
      name: 'Alice',
      project: environment.defaultProject,
      icon: '🧑‍💻',
      description: 'View sprint status, tickets, blockers and PR reviews',
    },
    {
      token: 'manager_token_bob',
      role: 'manager',
      name: 'Bob',
      project: environment.defaultProject,
      icon: '👔',
      description: 'Sprint risk, release decisions, admin panel access',
    },
    {
      token: 'stakeholder_token_client',
      role: 'stakeholder',
      name: 'Client',
      project: environment.defaultProject,
      icon: '🤝',
      description: 'High-level delivery updates and release readiness',
    },
  ];

  constructor(private auth: AuthService, private router: Router) { }

  selectRole(session: UserSession): void {
    this.auth.login(session);
    this.router.navigate([session.role === 'manager' ? '/admin' : '/chat']);
  }
}
