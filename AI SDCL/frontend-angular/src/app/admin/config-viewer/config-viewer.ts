import { Component, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { MatSelectModule } from '@angular/material/select';
import { MatButtonModule } from '@angular/material/button';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { MatIconModule } from '@angular/material/icon';
import { AdminService } from '../../core/services/admin.service';

@Component({
  selector: 'app-config-viewer',
  standalone: true,
  imports: [
    CommonModule, FormsModule,
    MatSelectModule, MatButtonModule, MatProgressSpinnerModule,
    MatSnackBarModule, MatIconModule,
  ],
  templateUrl: './config-viewer.html',
})
export class ConfigViewer {
  configKeys   = ['prompts', 'agents', 'llm', 'mcp_registry', 'rag_sources', 'redis', 'chunking'];
  selectedKey  = 'agents';
  configJson   = signal('');
  loading      = signal(false);
  reloading    = signal(false);

  constructor(private admin: AdminService, private snack: MatSnackBar) {
    this.loadConfig();
  }

  loadConfig(): void {
    this.loading.set(true);
    this.admin.getConfig(this.selectedKey).subscribe({
      next:  (res) => { this.configJson.set(JSON.stringify(res.config, null, 2)); this.loading.set(false); },
      error: ()    => { this.loading.set(false); },
    });
  }

  reloadAll(): void {
    this.reloading.set(true);
    this.admin.reloadConfig().subscribe({
      next: (res) => {
        this.reloading.set(false);
        this.snack.open(res.message, 'OK', { duration: 3000 });
        this.loadConfig();
      },
      error: () => { this.reloading.set(false); },
    });
  }
}
