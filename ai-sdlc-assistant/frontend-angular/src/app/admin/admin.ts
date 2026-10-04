import { Component, signal } from '@angular/core';
import { RouterOutlet, RouterLink, RouterLinkActive } from '@angular/router';
import { MatSidenavModule } from '@angular/material/sidenav';
import { MatListModule } from '@angular/material/list';
import { MatIconModule } from '@angular/material/icon';
import { MatButtonModule } from '@angular/material/button';
import { MatTooltipModule } from '@angular/material/tooltip';

@Component({
  selector: 'app-admin',
  standalone: true,
  imports: [
    RouterOutlet, RouterLink, RouterLinkActive,
    MatSidenavModule, MatListModule, MatIconModule, MatButtonModule, MatTooltipModule,
  ],
  templateUrl: './admin.html',
  styleUrl: './admin.css',
})
export class Admin {
  // E12 (requested) — sidebar collapse. mode="side" + [opened] is MatSidenav's
  // own built-in open/close transition (no GSAP/MotionService needed); the
  // global prefers-reduced-motion rule in styles.css already covers it.
  collapsed = signal(false);

  toggleSidenav(): void {
    this.collapsed.update(v => !v);
  }
}
