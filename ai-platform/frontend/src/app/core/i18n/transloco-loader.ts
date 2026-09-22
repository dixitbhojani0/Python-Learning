import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Translation, TranslocoLoader } from '@jsverse/transloco';

/**
 * Loads locale dictionaries from /i18n/<lang>.json at runtime (not baked into
 * the build) — a tenant's locale choice is config, resolved when the app
 * loads, never a separate per-language build (§24 of the platform blueprint).
 */
@Injectable({ providedIn: 'root' })
export class TranslocoHttpLoader implements TranslocoLoader {
  constructor(private http: HttpClient) {}

  getTranslation(lang: string) {
    return this.http.get<Translation>(`/i18n/${lang}.json`);
  }
}
