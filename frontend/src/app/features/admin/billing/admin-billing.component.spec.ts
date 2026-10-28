import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';
import { AdminPaymentSettings, BillingOverview, PaymentRecord } from '../../../core/models/billing.models';
import { AdminBillingService } from '../../../core/services/admin-billing.service';
import { AdminBillingComponent } from './admin-billing.component';

const SETTINGS: AdminPaymentSettings = {
  payments_enabled: true,
  active_gateway: 'cashfree',
  checkout_available: true,
  updated_at: null,
  updated_by_user_id: null,
  gateways: [
    { key: 'cashfree', display_name: 'Cashfree', enabled: true, is_default: true, configured: true, currencies: ['INR'] },
    { key: 'razorpay', display_name: 'Razorpay', enabled: false, is_default: false, configured: true, currencies: ['INR'] },
  ],
};

describe('AdminBillingComponent (online payments & gateway)', () => {
  let api: jasmine.SpyObj<AdminBillingService>;

  beforeEach(() => {
    api = jasmine.createSpyObj<AdminBillingService>('AdminBillingService', [
      'plans', 'currencies', 'overview', 'paymentSettings', 'updatePaymentSettings',
    ]);
    api.plans.and.returnValue(of([]));
    api.currencies.and.returnValue(of([]));
    api.overview.and.returnValue(of(null as unknown as BillingOverview));
    api.paymentSettings.and.returnValue(of(SETTINGS));
    TestBed.configureTestingModule({
      imports: [AdminBillingComponent],
      providers: [{ provide: AdminBillingService, useValue: api }],
    });
  });

  function render() {
    const fixture = TestBed.createComponent(AdminBillingComponent);
    fixture.detectChanges();
    fixture.componentInstance.setView('plans');
    fixture.detectChanges();
    return fixture;
  }

  const siteSwitch = (el: HTMLElement) => el.querySelector('.ps-switch-row .switch') as HTMLButtonElement;
  const gwSwitch = (el: HTMLElement, i: number) => el.querySelectorAll('.gw-card .switch')[i] as HTMLButtonElement;

  it('shows the site switch and both gateways, each with its own switch and the default marked', () => {
    const el: HTMLElement = render().nativeElement;
    expect(siteSwitch(el).getAttribute('aria-checked')).toBe('true');
    const cards = [...el.querySelectorAll('.gw-card')] as HTMLElement[];
    expect(cards.map((c) => c.querySelector('strong')?.textContent?.trim())).toEqual(['Cashfree', 'Razorpay']);
    expect(gwSwitch(el, 0).getAttribute('aria-checked')).toBe('true');
    expect(gwSwitch(el, 1).getAttribute('aria-checked')).toBe('false');
    expect(gwSwitch(el, 1).disabled).toBeFalse();
    expect(cards[0].textContent).toContain('Default');
    expect(cards[1].textContent).toContain('Make default');
    expect(el.querySelector('.pay-chip')?.textContent).toContain('Payments on · Cashfree');
  });

  it('turns payments off after confirmation and shows the new state', () => {
    spyOn(window, 'confirm').and.returnValue(true);
    api.updatePaymentSettings.and.returnValue(of({ ...SETTINGS, payments_enabled: false, checkout_available: false }));
    const fixture = render();
    siteSwitch(fixture.nativeElement).click();
    fixture.detectChanges();
    expect(api.updatePaymentSettings).toHaveBeenCalledWith({ payments_enabled: false, reason: '' });
    expect(fixture.nativeElement.querySelector('.pay-chip').textContent).toContain('Payments off');
  });

  it('does nothing when the admin cancels turning payments off', () => {
    spyOn(window, 'confirm').and.returnValue(false);
    const fixture = render();
    siteSwitch(fixture.nativeElement).click();
    expect(api.updatePaymentSettings).not.toHaveBeenCalled();
  });

  it('switches Razorpay on alongside Cashfree', () => {
    api.updatePaymentSettings.and.returnValue(of({
      ...SETTINGS, gateways: [SETTINGS.gateways[0], { ...SETTINGS.gateways[1], enabled: true }],
    }));
    const fixture = render();
    gwSwitch(fixture.nativeElement, 1).click();
    fixture.detectChanges();
    expect(api.updatePaymentSettings).toHaveBeenCalledWith({ gateways_enabled: { razorpay: true }, reason: '' });
    expect(fixture.nativeElement.querySelector('.pay-chip').textContent).toContain('Cashfree + Razorpay');
  });

  it('makes Razorpay the default', () => {
    api.updatePaymentSettings.and.returnValue(of({ ...SETTINGS, active_gateway: 'razorpay' }));
    const fixture = render();
    const make = fixture.nativeElement.querySelectorAll('.gw-card')[1].querySelector('.link') as HTMLButtonElement;
    make.click();
    expect(api.updatePaymentSettings).toHaveBeenCalledWith({ active_gateway: 'razorpay', reason: '' });
  });

  it('lets Razorpay be switched on without keys and warns it is not offered yet', () => {
    const unconfigured = {
      ...SETTINGS,
      gateways: [SETTINGS.gateways[0], { ...SETTINGS.gateways[1], enabled: true, configured: false }],
    };
    api.paymentSettings.and.returnValue(of(unconfigured));
    const el: HTMLElement = render().nativeElement;
    expect(gwSwitch(el, 1).disabled).toBeFalse();
    expect(el.querySelectorAll('.gw-card')[1].textContent).toContain('Keys not set');
    expect(el.querySelector('.pay-settings .banner')?.textContent).toContain('Razorpay');
  });

  it('refuses to switch off the only gateway that is on', () => {
    const fixture = render();
    gwSwitch(fixture.nativeElement, 0).click();
    expect(api.updatePaymentSettings).not.toHaveBeenCalled();
    expect(fixture.componentInstance.error()).toContain('at least one gateway');
  });
});

describe('AdminBillingComponent (refunds & chargebacks)', () => {
  let api: jasmine.SpyObj<AdminBillingService>;

  const PAY: PaymentRecord = {
    id: 5, user_id: 7, user_email: 'a@b.c', plan_id: 3, plan_code: '1-month', plan_name: 'Monthly Plan',
    duration_days: 30, duration_hours: 720, screenshot_allowance: 240, amount_minor: 99900, currency: 'INR',
    gateway: 'cashfree', receipt: 'oi_5', gateway_order_id: 'oi_5', gateway_payment_id: '9001', status: 'paid',
    method: 'upi', failure_reason: '', verified_via: 'webhook', is_renewal: false, created_at: null, paid_at: null,
    refunded_at: null, refund_reference: '', subscription_id: 1,
  };
  const OVERVIEW = {
    date_from: null, date_to: null, total_users: 1, active_subscriptions: 1, expired_subscriptions: 0,
    ended_subscriptions: 0, payments_by_status: { paid: 1 }, revenue_by_currency: [], refunds_by_currency: [],
    subscriptions_by_plan: {}, screenshot_requests: 0, screenshots_analyzed: 0, screenshot_failures: 0,
    payment_success_rate: 1, disputes_count: 1, dispute_rate: 0.012, dispute_value_rate_inr: 0.012,
    open_disputes: 1, next_dispute_deadline: '2026-11-05T18:29:59Z',
  } as BillingOverview;

  beforeEach(() => {
    api = jasmine.createSpyObj<AdminBillingService>('AdminBillingService', [
      'plans', 'currencies', 'overview', 'paymentSettings', 'paymentDetail', 'refund', 'evidence',
    ]);
    api.plans.and.returnValue(of([]));
    api.currencies.and.returnValue(of([]));
    api.overview.and.returnValue(of(OVERVIEW));
    api.paymentSettings.and.returnValue(of(SETTINGS));
    api.paymentDetail.and.callFake(() => of({ payment: PAY, subscription: null, webhook_events: [] }));
    TestBed.configureTestingModule({
      imports: [AdminBillingComponent],
      providers: [{ provide: AdminBillingService, useValue: api }],
    });
  });

  function render() {
    const fixture = TestBed.createComponent(AdminBillingComponent);
    fixture.detectChanges();
    return fixture;
  }

  it('flags a chargeback ratio above 1% and shows open disputes with the deadline', () => {
    const fixture = render();
    const kpis = [...fixture.nativeElement.querySelectorAll('.kpi')] as HTMLElement[];
    const ratio = kpis.find((k) => k.textContent?.includes('Chargeback ratio'))!;
    expect(ratio.textContent).toContain('1.2%');
    expect(ratio.querySelector('strong')?.classList).toContain('danger');
    expect(kpis.find((k) => k.textContent?.includes('Open chargebacks'))?.textContent).toContain('next deadline');
    expect(fixture.componentInstance.disputeLevel({ ...OVERVIEW, dispute_value_rate_inr: 0.006 })).toBe('warn');
    expect(fixture.componentInstance.disputeLevel({ ...OVERVIEW, dispute_value_rate_inr: 0.001 })).toBe('ok');
  });

  it('refund needs a reason and a confirmation, then calls the API once', () => {
    const fixture = render();
    const cmp = fixture.componentInstance;
    api.refund.and.returnValue(of({ status: 'refunded', payment_id: 5, subscription_id: 1, message: 'Refund initiated; access ended.' }));
    const confirm = spyOn(window, 'confirm').and.returnValue(false);
    cmp.refundPayment(PAY);
    expect(cmp.error()).toContain('refund reason');
    cmp.refundReason = 'Customer asked';
    cmp.refundPayment(PAY);
    expect(confirm).toHaveBeenCalled();
    expect(api.refund).not.toHaveBeenCalled();
    confirm.and.returnValue(true);
    cmp.refundPayment(PAY);
    expect(api.refund).toHaveBeenCalledOnceWith(5, 'Customer asked');
    expect(cmp.info()).toContain('Refund initiated');
  });

  it('shows refund and evidence actions on a paid Cashfree payment, evidence only when disputed', () => {
    const fixture = render();
    fixture.componentInstance.openPayment(PAY);
    fixture.detectChanges();
    let actions = (fixture.nativeElement.querySelector('.ab-actions') as HTMLElement).textContent!;
    expect(actions).toContain('Refund ₹999');
    expect(actions).toContain('Download dispute evidence');
    api.paymentDetail.and.returnValue(of({
      payment: { ...PAY, status: 'disputed', disputed_at: '2026-10-28T00:00:00Z', dispute_status: 'CHARGEBACK_CREATED' },
      subscription: null, webhook_events: [],
    }));
    fixture.componentInstance.openPayment(PAY);
    fixture.detectChanges();
    actions = (fixture.nativeElement.querySelector('.ab-actions') as HTMLElement).textContent!;
    expect(actions).not.toContain('Refund ₹');
    expect(actions).toContain('Download dispute evidence');
    expect(fixture.nativeElement.textContent).toContain('CHARGEBACK_CREATED');
  });
});
