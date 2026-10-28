import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { AuthService } from '../../core/auth/auth.service';
import { Plan, durationLabel, formatMoney, withoutScreenshotCopy } from '../../core/models/billing.models';
import { CheckoutLauncherService } from '../../core/payments/checkout-launcher.service';
import { PricingComponent } from './pricing.component';

const PLAN: Plan = {
  id: 3, code: '1-month', name: '1 Month', description: '30-day access with 240 screenshot analyses. Suitable for regular Call preparation.', currency: 'INR',
  duration_days: 30, duration_hours: 720, duration_unit: 'days', screenshot_allowance: 240,
  billing_type: 'recurring', billing_interval_days: 30, auto_renew_available: false, available: true, payable: true,
  trial_days: 0, price_minor: 90000, regular_price_minor: 90000,
  is_promo: false, promo_label: '', promo_ends_at: null, features: ['Real-time AI answer guidance'],
  badge: 'Most popular', is_highlighted: true,
};

describe('PricingComponent (database-driven)', () => {
  let http: HttpTestingController;
  let launcher: jasmine.SpyObj<CheckoutLauncherService>;
  let authenticated = true;

  beforeEach(() => {
    authenticated = true;
    launcher = jasmine.createSpyObj('CheckoutLauncherService', ['open']);
    TestBed.configureTestingModule({
      imports: [PricingComponent],
      providers: [
        provideRouter([]),
        provideHttpClient(),
        provideHttpClientTesting(),
        { provide: CheckoutLauncherService, useValue: launcher },
        { provide: AuthService, useValue: { get isAuthenticated() { return authenticated; }, currentUser: { email: 'a@b.c' } } },
      ],
    });
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  const CURRENCIES = [
    { code: 'INR', name: 'Indian Rupee', minor_digits: 2, display_enabled: true, payments_enabled: true,
      payable_now: true, gateways: ['mock'], sort_order: 10, live_prices: 1 },
    { code: 'USD', name: 'US Dollar', minor_digits: 2, display_enabled: true, payments_enabled: false,
      payable_now: false, gateways: [], sort_order: 20, live_prices: 0 },
  ];

  const OPEN = { payments_enabled: true, active_gateway: 'cashfree', active_gateway_name: 'Cashfree', available: true, message: '' };

  function render(plans: Plan[] = [PLAN], status: object = OPEN) {
    try { localStorage.setItem('oi_currency', 'INR'); } catch { /* ignore */ }
    const fixture = TestBed.createComponent(PricingComponent);
    fixture.detectChanges();
    http.expectOne('/api/payments/status').flush(status);
    http.expectOne('/api/pricing/currencies').flush(CURRENCIES);
    http.expectOne('/api/payments/gateways').flush([{ key: 'mock', display_name: 'Test', currencies: ['INR', 'USD'] }]);
    http.expectOne('/api/plans?currency=INR').flush(plans);
    fixture.detectChanges();
    return fixture;
  }

  it('renders plans, prices, duration from the API, with no auto-renewal claim', () => {
    const el: HTMLElement = render().nativeElement;
    expect(el.textContent).toContain('1 Month');
    expect(el.textContent).toContain('₹900');
    expect(el.textContent).toContain('Most popular');
    expect(el.textContent).toContain('30 days');
    expect(el.textContent).toContain('no auto-renewal');
    expect(el.textContent).not.toMatch(/auto-renews/i);
  });

  it('shows per-day cost for each plan and no screenshot text on the pricing page', () => {
    const el: HTMLElement = render().nativeElement;
    const text = el.textContent ?? '';
    expect(text).toContain(formatMoney(3000, 'INR')); // 90000 paise / 30 days
    expect(text).toContain('per day');
    expect(text).not.toMatch(/screenshot/i);
  });

  it('separates one-time passes from longer plans', () => {
    const pass = { ...PLAN, id: 1, code: '1-day', name: '1 Day Pass', billing_type: 'one_time', duration_hours: 24, duration_unit: 'hours', badge: '', is_highlighted: false };
    const el: HTMLElement = render([pass, PLAN]).nativeElement;
    const groups = el.querySelectorAll('.group');
    expect(groups.length).toBe(2);
    expect(groups[0].textContent).toContain('1 Day Pass');
    expect(groups[0].textContent).toContain('24 hours');
    expect(groups[1].textContent).toContain('1 Month');
  });

  it('switching currency refetches server prices and never offers unpriced plans', () => {
    const fixture = render();
    fixture.componentInstance.setCurrency('USD');
    http.expectOne('/api/plans?currency=USD').flush([{ ...PLAN, currency: 'USD', available: false, payable: false, price_minor: 0 }]);
    fixture.detectChanges();
    const el: HTMLElement = fixture.nativeElement;
    expect(el.textContent).toContain('Prices in USD are not available yet');
    const btn = el.querySelector('.plan .cta') as HTMLButtonElement;
    expect(btn.disabled).toBeTrue();
    try { localStorage.removeItem('oi_currency'); } catch { /* ignore */ }
  });

  it('sends signed-out visitors to register and back to pricing', () => {
    authenticated = false;
    const fixture = render();
    const nav = spyOn(TestBed.inject(Router), 'navigate');
    fixture.componentInstance.choose(PLAN);
    expect(nav).toHaveBeenCalledWith(['/register'], { queryParams: { redirect: '/pricing' } });
  });

  it('desktop app: payment continues on the website in the browser, no order is created in-app', async () => {
    const fixture = render();
    const cmp = fixture.componentInstance;
    (cmp as unknown as { isDesktop: boolean }).isDesktop = true;
    const open = spyOn(window, 'open').and.returnValue(null);
    cmp.choose(PLAN);
    fixture.detectChanges();
    const el: HTMLElement = fixture.nativeElement;
    expect(el.querySelector('.checkout .btn-primary')?.textContent).toContain('Continue to payment in browser');
    expect(el.querySelector('.checkout')?.textContent).toContain('completed on our website in your web browser');
    await cmp.pay();
    expect(open).toHaveBeenCalledWith('https://www.oyeinterview.com/pricing', '_blank', 'noopener');
    http.expectNone('/api/payments/checkout');
    expect(launcher.open).not.toHaveBeenCalled();
    expect(cmp.message()).toContain('Checkout opened in your web browser');
  });

  it('web build is unaffected: no browser hand-off', () => {
    expect(render().componentInstance.isDesktop).toBeFalse();
  });

  it('checkout -> gateway -> server verification -> billing page', async () => {
    const fixture = render();
    const cmp = fixture.componentInstance;
    const nav = spyOn(TestBed.inject(Router), 'navigate');
    launcher.open.and.resolveTo({ kind: 'completed', payload: { order_id: 'o1', payment_id: 'p', signature: 's' } });
    cmp.choose(PLAN);
    const done = cmp.pay();
    const co = http.expectOne('/api/payments/checkout');
    // No price from the browser: only plan, gateway, currency and a per-attempt key.
    expect(co.request.body).toEqual({
      plan_id: 3, gateway: 'mock', phone: '', currency: 'INR', idempotency_key: jasmine.stringMatching(/^chk-/),
    });
    co.flush({ payment_id: 1, gateway: 'mock', order_id: 'o1', amount_minor: 90000, currency: 'INR', checkout: {} });
    await Promise.resolve();
    await new Promise((r) => setTimeout(r));
    const v = http.expectOne('/api/payments/mock/verify');
    expect(v.request.body.order_id).toBe('o1');
    v.flush({ status: 'paid', payment_id: 1, subscription_id: 9, message: '' });
    await done;
    expect(nav).toHaveBeenCalledWith(['/billing'], { queryParams: { purchased: 1 } });
  });

  it('a failed server verification shows an error and does not navigate', async () => {
    const fixture = render();
    const cmp = fixture.componentInstance;
    const nav = spyOn(TestBed.inject(Router), 'navigate');
    launcher.open.and.resolveTo({ kind: 'completed', payload: {} });
    cmp.choose(PLAN);
    const done = cmp.pay();
    http.expectOne('/api/payments/checkout').flush({ payment_id: 1, gateway: 'mock', order_id: 'o1', amount_minor: 90000, currency: 'INR', checkout: {} });
    await new Promise((r) => setTimeout(r));
    http.expectOne('/api/payments/mock/verify').flush({ status: 'failed', payment_id: 1, subscription_id: null, message: 'Payment signature is invalid.' });
    await done;
    expect(nav).not.toHaveBeenCalled();
    expect(cmp.message()).toContain('signature');
    expect(cmp.messageKind()).toBe('error');
  });
});

describe('PricingComponent (admin payment switch)', () => {
  let http: HttpTestingController;
  let launcher: jasmine.SpyObj<CheckoutLauncherService>;

  beforeEach(() => {
    launcher = jasmine.createSpyObj('CheckoutLauncherService', ['open']);
    TestBed.configureTestingModule({
      imports: [PricingComponent],
      providers: [
        provideRouter([]),
        provideHttpClient(),
        provideHttpClientTesting(),
        { provide: CheckoutLauncherService, useValue: launcher },
        { provide: AuthService, useValue: { isAuthenticated: true, currentUser: { email: 'a@b.c' } } },
      ],
    });
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  function render(status: object, gateways: object[]) {
    try { localStorage.setItem('oi_currency', 'INR'); } catch { /* ignore */ }
    const fixture = TestBed.createComponent(PricingComponent);
    fixture.detectChanges();
    http.expectOne('/api/payments/status').flush(status);
    http.expectOne('/api/pricing/currencies').flush([
      { code: 'INR', name: 'Indian Rupee', minor_digits: 2, display_enabled: true, payments_enabled: true,
        payable_now: gateways.length > 0, gateways: gateways.map((g) => (g as { key: string }).key), sort_order: 10, live_prices: 1 },
    ]);
    http.expectOne('/api/payments/gateways').flush(gateways);
    http.expectOne('/api/plans?currency=INR').flush([{ ...PLAN, payable: gateways.length > 0 }]);
    fixture.detectChanges();
    return fixture;
  }

  it('shows a clear "temporarily unavailable" state and never starts checkout when payments are off', () => {
    const fixture = render(
      { payments_enabled: false, active_gateway: 'cashfree', active_gateway_name: '', available: false,
        message: 'Online payments are temporarily unavailable. Please try again later.' },
      [],
    );
    const el: HTMLElement = fixture.nativeElement;
    expect(el.querySelector('.payments-off')?.textContent).toContain('temporarily unavailable');
    const btn = el.querySelector('.plan .cta') as HTMLButtonElement;
    expect(btn.disabled).toBeTrue();
    expect(btn.textContent).toContain('Payments temporarily unavailable');
    fixture.componentInstance.choose(PLAN);
    expect(fixture.componentInstance.selected()).toBeNull();
    expect(launcher.open).not.toHaveBeenCalled();
  });

  it('checks out through the gateway the admin selected (Razorpay) without asking for a phone', async () => {
    const fixture = render(
      { payments_enabled: true, active_gateway: 'razorpay', active_gateway_name: 'Razorpay', available: true, message: '' },
      [{ key: 'razorpay', display_name: 'Razorpay', currencies: ['INR'] }],
    );
    const cmp = fixture.componentInstance;
    expect((fixture.nativeElement as HTMLElement).textContent).toContain('payment partner, Razorpay');
    launcher.open.and.resolveTo({ kind: 'dismissed' });
    cmp.choose(PLAN);
    expect(cmp.needsPhone()).toBeFalse();
    const done = cmp.pay();
    const co = http.expectOne('/api/payments/checkout');
    expect(co.request.body.gateway).toBe('razorpay');
    co.flush({ payment_id: 1, gateway: 'razorpay', order_id: 'o1', amount_minor: 90000, currency: 'INR', checkout: {} });
    await done;
    expect(launcher.open).toHaveBeenCalled();
  });

  it('offers both gateways when the admin turned both on, default pre-selected', () => {
    const fixture = render(
      { payments_enabled: true, active_gateway: 'razorpay', active_gateway_name: 'Razorpay',
        gateway_names: ['Razorpay', 'Cashfree'], available: true, message: '' },
      [
        { key: 'razorpay', display_name: 'Razorpay', currencies: ['INR'] },
        { key: 'cashfree', display_name: 'Cashfree', currencies: ['INR'] },
      ],
    );
    const cmp = fixture.componentInstance;
    cmp.choose(PLAN);
    fixture.detectChanges();
    const el: HTMLElement = fixture.nativeElement;
    expect([...el.querySelectorAll('.gw-opt')].map((x) => x.textContent?.trim())).toEqual(['Razorpay', 'Cashfree']);
    expect(cmp.gateway).toBe('razorpay');
    expect(el.textContent).toContain('payment partners, Razorpay and Cashfree');
    cmp.onGatewayChange('cashfree');
    expect(cmp.needsPhone()).toBeTrue();
  });

  it('flips to the unavailable state when checkout returns 503 (switched off mid-session)', async () => {
    const fixture = render(
      { payments_enabled: true, active_gateway: 'cashfree', active_gateway_name: 'Cashfree', available: true, message: '' },
      [{ key: 'mock', display_name: 'Test', currencies: ['INR'] }],
    );
    const cmp = fixture.componentInstance;
    cmp.choose(PLAN);
    const done = cmp.pay();
    http.expectOne('/api/payments/checkout').flush(
      { detail: 'Online payments are temporarily unavailable. Please try again later.' },
      { status: 503, statusText: 'Service Unavailable' },
    );
    await done;
    expect(cmp.paymentsOff()).toBeTrue();
    expect(launcher.open).not.toHaveBeenCalled();
  });
});

describe('billing formatters', () => {
  it('drops screenshot wording from plan descriptions only for display', () => {
    expect(withoutScreenshotCopy('24-hour access with 8 screenshot analyses. Suitable for a single Call.'))
      .toBe('24-hour access. Suitable for a single Call.');
    expect(withoutScreenshotCopy('Coverage')).toBe('Coverage');
  });

  it('formats minor units as rupees', () => {
    expect(formatMoney(15000)).toBe('₹150');
    expect(formatMoney(200000)).toBe('₹2,000');
    expect(formatMoney(12345)).toBe('₹123.45');
  });

  it('labels durations', () => {
    expect(durationLabel(3)).toBe('3 days');
    expect(durationLabel(7)).toBe('1 week');
    expect(durationLabel(30)).toBe('1 month');
    expect(durationLabel(90)).toBe('3 months');
  });
});
