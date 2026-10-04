import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { Admin } from './admin';

describe('Admin', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [Admin],
      providers: [provideRouter([])],
    }).compileComponents();
  });

  it('starts with the sidebar expanded', () => {
    const fixture = TestBed.createComponent(Admin);
    expect(fixture.componentInstance.collapsed()).toBe(false);
  });

  it('toggleSidenav collapses and expands the sidebar', () => {
    const fixture = TestBed.createComponent(Admin);
    const admin = fixture.componentInstance;

    admin.toggleSidenav();
    expect(admin.collapsed()).toBe(true);

    admin.toggleSidenav();
    expect(admin.collapsed()).toBe(false);
  });
});
