import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { AuthService } from '../../core/auth/auth.service';
import { Plan, durationLabel, formatMoney } from '../../core/models/billing.models';
import { CheckoutLauncherService } from '../../core/payments/checkout-launcher.service';
import { PricingComponent } from './pricing.component';

const PLAN: Plan = {
  id: 3, code: '1-month', name: '1 Month', description: 'Coverage', currency: 'INR',
  duration_days: 30, trial_days: 0, price_minor: 90000, regular_price_minor: 90000,
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

  function render() {
    const fixture = TestBed.createComponent(PricingComponent);
    fixture.detectChanges();
    http.expectOne('/api/plans').flush([PLAN]);
    http.expectOne('/api/payments/gateways').flush([{ key: 'mock', display_name: 'Test' }]);
    fixture.detectChanges();
    return fixture;
  }

  it('renders plans and prices from the API, with no auto-renewal claim', () => {
    const el: HTMLElement = render().nativeElement;
    expect(el.textContent).toContain('1 Month');
    expect(el.textContent).toContain('₹900');
    expect(el.textContent).toContain('Most popular');
    expect(el.textContent).not.toMatch(/auto-renews/i);
  });

  it('sends signed-out visitors to register and back to pricing', () => {
    authenticated = false;
    const fixture = render();
    const nav = spyOn(TestBed.inject(Router), 'navigate');
    fixture.componentInstance.choose(PLAN);
    expect(nav).toHaveBeenCalledWith(['/register'], { queryParams: { redirect: '/pricing' } });
  });

  it('checkout -> gateway -> server verification -> billing page', async () => {
    const fixture = render();
    const cmp = fixture.componentInstance;
    const nav = spyOn(TestBed.inject(Router), 'navigate');
    launcher.open.and.resolveTo({ kind: 'completed', payload: { order_id: 'o1', payment_id: 'p', signature: 's' } });
    cmp.choose(PLAN);
    const done = cmp.pay();
    const co = http.expectOne('/api/payments/checkout');
    expect(co.request.body).toEqual({ plan_id: 3, gateway: 'mock', phone: '' }); // no price from the browser
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

describe('billing formatters', () => {
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
