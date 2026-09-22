import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TranslocoTestingModule } from '@jsverse/transloco';
import { HealthComponent } from './health';

const LANGS = {
  en: {
    health: {
      title: 'Platform status',
      checking: 'Checking service health…',
      ok: 'Everything is healthy.',
      error: 'Could not reach the platform right now. Please try again shortly.',
      localeLabel: 'Language',
    },
  },
  hi: {
    health: {
      title: 'प्लेटफ़ॉर्म स्थिति',
      checking: 'सेवा की जांच की जा रही है…',
      ok: 'सब कुछ सही से कार्य कर रहा है।',
      error: 'अभी प्लेटफ़ॉर्म तक नहीं पहुंच सके।',
      localeLabel: 'भाषा',
    },
  },
};

describe('HealthComponent', () => {
  let httpMock: HttpTestingController;

  async function setup() {
    await TestBed.configureTestingModule({
      imports: [
        HealthComponent,
        TranslocoTestingModule.forRoot({
          langs: LANGS,
          preloadLangs: true,
          translocoConfig: { availableLangs: ['en', 'hi'], defaultLang: 'en', reRenderOnLangChange: true },
        }),
      ],
      providers: [provideHttpClient(), provideHttpClientTesting()],
    }).compileComponents();

    httpMock = TestBed.inject(HttpTestingController);
    const fixture = TestBed.createComponent(HealthComponent);
    fixture.detectChanges();
    return fixture;
  }

  afterEach(() => httpMock.verify());

  // ── Positive ────────────────────────────────────────────────────────────

  it('shows the translated checking state before the backend responds', async () => {
    const fixture = await setup();
    const text = (fixture.nativeElement as HTMLElement).querySelector('[data-testid="health-message"]')!.textContent;
    expect(text).toContain('Checking service health');

    httpMock.expectOne('/api/health').flush({ status: 'ok', message: 'Service is healthy.' });
  });

  it('shows the translated ok state and the backend message once the request succeeds', async () => {
    const fixture = await setup();
    httpMock.expectOne('/api/health').flush({ status: 'ok', message: 'Service is healthy.' });
    fixture.detectChanges();

    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="health-message"]')!.textContent).toContain('Everything is healthy.');
    expect(el.querySelector('[data-testid="backend-message"]')!.textContent).toContain('Service is healthy.');
  });

  // ── i18n (the point of this slice) ─────────────────────────────────────

  it('re-renders in a different language at runtime, with no reload/rebuild', async () => {
    const fixture = await setup();
    httpMock.expectOne('/api/health').flush({ status: 'ok', message: 'Service is healthy.' });
    fixture.detectChanges();

    const component = fixture.componentInstance as unknown as { setLocale: (lang: string) => void };
    component.setLocale('hi');
    await fixture.whenStable(); // setActiveLang resolves the new lang asynchronously, even when preloaded
    fixture.detectChanges();

    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="health-message"]')!.textContent).toContain('सब कुछ सही से कार्य कर रहा है');
  });

  // ── Negative ────────────────────────────────────────────────────────────

  it('shows a translated error state on a failed request — never a raw error or blank screen', async () => {
    const fixture = await setup();
    httpMock.expectOne('/api/health').flush('boom', { status: 500, statusText: 'Server Error' });
    fixture.detectChanges();

    const el = fixture.nativeElement as HTMLElement;
    const message = el.querySelector('[data-testid="health-message"]');
    expect(message!.textContent).toContain('Could not reach the platform');
    expect(message!.getAttribute('role')).toBe('alert');
  });

  // ── Edge / side effects ────────────────────────────────────────────────

  it('recovers cleanly to the ok state on a manual refresh after an earlier error', async () => {
    const fixture = await setup();
    httpMock.expectOne('/api/health').flush('boom', { status: 500, statusText: 'Server Error' });
    fixture.detectChanges();

    (fixture.componentInstance as unknown as { refresh: () => void }).refresh();
    httpMock.expectOne('/api/health').flush({ status: 'ok', message: 'Service is healthy.' });
    fixture.detectChanges();

    const el = fixture.nativeElement as HTMLElement;
    // The earlier error state must not linger alongside the new ok state.
    expect(el.querySelector('[data-testid="health-message"]')!.textContent).toContain('Everything is healthy.');
    expect(el.textContent).not.toContain('Could not reach the platform');
  });
});
