import { DatePipe } from '@angular/common';
import { Component, inject, signal } from '@angular/core';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { TranslocoPipe } from '@jsverse/transloco';
import { AdminService, DocumentDetail } from '../../../core/admin/admin.service';

@Component({
  selector: 'app-admin-document-detail',
  imports: [TranslocoPipe, RouterLink, DatePipe],
  templateUrl: './document-detail.html',
})
export class AdminDocumentDetailComponent {
  private readonly admin = inject(AdminService);
  private readonly route = inject(ActivatedRoute);

  protected readonly document = signal<DocumentDetail | null>(null);
  protected readonly error = signal<string | null>(null);

  constructor() {
    this.load(this.route.snapshot.paramMap.get('id')!);
  }

  private async load(id: string): Promise<void> {
    try {
      this.document.set(await this.admin.getDocument(id));
    } catch {
      this.error.set('admin.documentLoadFailed');
    }
  }
}
