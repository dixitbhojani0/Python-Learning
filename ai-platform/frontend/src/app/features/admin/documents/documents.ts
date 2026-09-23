import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { TranslocoPipe } from '@jsverse/transloco';
import { AdminService, DocumentSummary } from '../../../core/admin/admin.service';

@Component({
  selector: 'app-admin-documents',
  imports: [FormsModule, TranslocoPipe, RouterLink],
  templateUrl: './documents.html',
})
export class AdminDocumentsComponent {
  private readonly admin = inject(AdminService);

  protected readonly documents = signal<DocumentSummary[]>([]);
  protected readonly documentsError = signal<string | null>(null);

  protected ingestTitle = '';
  protected ingestContent = '';
  protected readonly ingesting = signal(false);
  protected readonly ingestError = signal<string | null>(null);

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

  protected async ingest(): Promise<void> {
    const title = this.ingestTitle.trim();
    const content = this.ingestContent.trim();
    if (!title || !content || this.ingesting()) {
      return;
    }

    this.ingesting.set(true);
    this.ingestError.set(null);
    try {
      await this.admin.ingestDocument(title, content);
      this.ingestTitle = '';
      this.ingestContent = '';
      await this.loadDocuments();
    } catch {
      this.ingestError.set('admin.ingestFailed');
    } finally {
      this.ingesting.set(false);
    }
  }
}
