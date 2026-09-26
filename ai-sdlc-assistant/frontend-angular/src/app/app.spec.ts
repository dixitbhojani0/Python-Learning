import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { App } from './app';

describe('App', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [App],
      providers: [provideRouter([])],
    }).compileComponents();
  });

  it('should create the app', () => {
    const fixture = TestBed.createComponent(App);
    const app = fixture.componentInstance;
    expect(app).toBeTruthy();
  });

  // The scaffold "renders Hello title" test went stale the day the shell became
  // a toolbar + router-outlet. Assert the real shell instead: toolbar only when
  // a session exists in localStorage.
  it('shows the app toolbar when a session exists', async () => {
    localStorage.setItem('sdlc_session', JSON.stringify({
      name: 'Test', role: 'developer', token: 'dev_token_alice', project: 'SDLC',
    }));
    try {
      const fixture = TestBed.createComponent(App);
      await fixture.whenStable();
      fixture.detectChanges();
      const compiled = fixture.nativeElement as HTMLElement;
      expect(compiled.textContent).toContain('AI SDLC Assistant');
    } finally {
      localStorage.removeItem('sdlc_session');
    }
  });
});
