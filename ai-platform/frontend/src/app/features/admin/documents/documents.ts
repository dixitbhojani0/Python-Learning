import { Component, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { TranslocoPipe } from '@jsverse/transloco';
import { AdminService, DocumentSummary } from '../../../core/admin/admin.service';

@Component({
  selector: 'app-admin-documents',
  imports: [TranslocoPipe, RouterLink],
  templateUrl: './documents.html',
})
export class AdminDocumentsComponent {
  private readonly admin = inject(AdminService);

  protected readonly documents = signal<DocumentSummary[]>([]);
  protected readonly documentsError = signal<string | null>(null);

  constructor() {
    this.loadDocuments();
  }

  private async loadDocuments(): Promise<void> {
    try {
      this.documents.set(await this.admin.listDocuments());
    } catch {
      this.documentsError.set('admin.accessDenied');
    }
  }

  protected async deleteDocument(id: string): Promise<void> {
    try {
      await this.admin.deleteDocument(id);
      this.documents.update((current) => current.filter((doc) => doc.id !== id));
    } catch {
      this.documentsError.set('admin.deleteFailed');
    }
  }
}
