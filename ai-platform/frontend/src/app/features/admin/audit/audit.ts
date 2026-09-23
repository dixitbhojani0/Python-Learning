import { DatePipe, JsonPipe } from '@angular/common';
import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { TranslocoPipe } from '@jsverse/transloco';
import { AdminService, AuditLogEntry } from '../../../core/admin/admin.service';

// Mirrors backend/app/api/admin_routes.py's KNOWN_AUDIT_EVENT_TYPES exactly —
// the real, complete set of event types anything in this codebase actually
// records (grepped from every record_event(...) call site). The category
// filter only ever offers values that can genuinely appear in the log.
const KNOWN_EVENT_TYPES = [
  'chat_turn',
  'tool_call',
  'tool_call_pending_approval',
  'tool_call_approved',
  'tool_call_rejected',
] as const;

@Component({
  selector: 'app-admin-audit',
  imports: [FormsModule, TranslocoPipe, DatePipe, JsonPipe],
  templateUrl: './audit.html',
})
export class AdminAuditComponent {
  private readonly admin = inject(AdminService);

  protected readonly knownEventTypes = KNOWN_EVENT_TYPES;
  protected selectedEventType = '';
  protected readonly events = signal<AuditLogEntry[]>([]);
  protected readonly error = signal<string | null>(null);
  protected readonly loading = signal(false);

  constructor() {
    this.load();
  }

  protected eventBadgeClass(eventType: string): string {
    switch (eventType) {
      case 'tool_call_rejected':
        return 'badge-danger';
      case 'tool_call_approved':
        return 'badge-success';
      case 'tool_call_pending_approval':
        return 'badge-warning';
      case 'chat_turn':
        return 'badge-info';
      default:
        return 'badge-neutral';
    }
  }

  protected async load(): Promise<void> {
    this.loading.set(true);
    this.error.set(null);
    try {
      this.events.set(await this.admin.listAuditLog(this.selectedEventType || null));
    } catch {
      this.error.set('admin.accessDenied');
    } finally {
      this.loading.set(false);
    }
  }
}
