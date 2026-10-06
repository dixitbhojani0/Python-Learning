import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';

import { HitlCard } from './hitl-card';
import { HitlService } from '../../core/services/hitl.service';
import { HITLResponse } from '../../core/models/api.models';

// E8 — Approve / Approve-all (this conversation) / Reject. Fakes the real
// HitlService's eligibility + "remembered this session" state instead of a
// spy framework, matching this repo's existing spec style (chat.spec.ts,
// admin.spec.ts both use plain objects, no vi.fn()/jasmine spies).
class FakeHitlService {
  approveCalls: Array<{ hitlId: string; remember: boolean }> = [];
  private eligible = new Set(['send_slack']);
  private autoApproved = new Set<string>();

  approve(hitlId: string, remember = false) {
    this.approveCalls.push({ hitlId, remember });
    return of({ response: 'done', decision: 'approved', hitl_id: hitlId } as HITLResponse);
  }

  reject() {
    return of({ response: 'rejected', decision: 'rejected', hitl_id: 'x' } as HITLResponse);
  }

  isApproveAllEligible(actionType: string): boolean {
    return this.eligible.has(actionType);
  }

  isAutoApproved(actionType: string): boolean {
    return this.autoApproved.has(actionType);
  }

  markAutoApproved(actionType: string): void {
    this.autoApproved.add(actionType);
  }
}

describe('HitlCard — approve / approve-all / reject (E8)', () => {
  let fakeHitl: FakeHitlService;

  beforeEach(async () => {
    fakeHitl = new FakeHitlService();
    await TestBed.configureTestingModule({
      imports: [HitlCard],
      providers: [{ provide: HitlService, useValue: fakeHitl }],
    }).compileComponents();
  });

  function render(actionType: string | null): { fixture: any; el: HTMLElement } {
    const fixture = TestBed.createComponent(HitlCard);
    fixture.componentInstance.hitlId = 'hitl-1';
    fixture.componentInstance.actionType = actionType;
    fixture.detectChanges();
    return { fixture, el: fixture.nativeElement as HTMLElement };
  }

  it('shows the Approve-all button for an eligible action type', () => {
    const { el } = render('send_slack');
    const buttons = Array.from(el.querySelectorAll('button')).map(b => b.textContent);
    expect(buttons.some(t => t?.includes('Approve all'))).toBe(true);
  });

  it('hides the Approve-all button for a non-eligible action type', () => {
    const { el } = render('create_ticket');
    const buttons = Array.from(el.querySelectorAll('button')).map(b => b.textContent);
    expect(buttons.some(t => t?.includes('Approve all'))).toBe(false);
  });

  it('clicking Approve-all sends remember=true and marks the action type auto-approved', () => {
    const { fixture } = render('send_slack');
    fixture.componentInstance.approveAll();

    expect(fakeHitl.approveCalls).toEqual([{ hitlId: 'hitl-1', remember: true }]);
    expect(fakeHitl.isAutoApproved('send_slack')).toBe(true);
  });

  it('auto-approves immediately (no buttons) when the action type was already remembered', () => {
    fakeHitl.markAutoApproved('send_slack');
    const { fixture, el } = render('send_slack');

    expect(fakeHitl.approveCalls).toEqual([{ hitlId: 'hitl-1', remember: false }]);
    expect(fixture.componentInstance.busy()).toBe(true);
    expect(el.querySelector('button')).toBeNull();
  });

  it('does not auto-approve a different, non-remembered action type', () => {
    fakeHitl.markAutoApproved('send_slack');
    const { fixture, el } = render('create_ticket');

    expect(fakeHitl.approveCalls).toEqual([]);
    expect(fixture.componentInstance.busy()).toBe(false);
    expect(el.querySelector('button')).not.toBeNull();
  });
});
