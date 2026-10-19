import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { ActivatedRoute, convertToParamMap, provideRouter } from '@angular/router';
import { routes } from '../../app.routes';
import { BillingComponent } from './billing.component';

const ME = {
  current: {
    id: 1, user_id: 7, user_email: 'a@b.c', plan_id: 3, plan_code: '1-month', plan_name: '1 Month',
    status: 'active', source: 'payment', starts_at: '2026-10-01T00:00:00Z', expires_at: '2099-01-01T00:00:00Z',
    amount_minor: 90000, currency: 'INR', is_renewal: false, payment_id: 5, notes: '', revoked_at: null, created_at: null,
  },
  access_ends_at: '2099-01-01T00:00:00Z',
  subscriptions: [],
  payments: [
    { id: 5, user_id: 7, user_email: 'a@b.c', plan_id: 3, plan_code: '1-month', plan_name: '1 Month', duration_days: 30,
      amount_minor: 90000, currency: 'INR', gateway: 'razorpay', receipt: 'r', gateway_order_id: 'order_1',
      gateway_payment_id: 'pay_1', status: 'paid', method: 'upi', failure_reason: '', verified_via: 'webhook',
      is_renewal: false, created_at: '2026-10-01T00:00:00Z', paid_at: '2026-10-01T00:00:00Z' },
  ],
  trial_used: false,
};

describe('BillingComponent', () => {
  let http: HttpTestingController;

  function setup(query: Record<string, string> = {}) {
    TestBed.configureTestingModule({
      imports: [BillingComponent],
      providers: [
        provideRouter([]),
        provideHttpClient(),
        provideHttpClientTesting(),
        { provide: ActivatedRoute, useValue: { snapshot: { queryParamMap: convertToParamMap(query) } } },
      ],
    });
    http = TestBed.inject(HttpTestingController);
    const fixture = TestBed.createComponent(BillingComponent);
    fixture.detectChanges();
    return fixture;
  }

  afterEach(() => http.verify());

  it("shows the user's own plan, validity and payments", () => {
    const fixture = setup();
    http.expectOne('/api/billing/me').flush(ME);
    http.expectOne('/api/plans').flush([]);
    fixture.detectChanges();
    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('1 Month');
    expect(text).toContain('days left');
    expect(text).toContain('₹900');
    expect(text).toContain('pay_1');
  });

  it('verifies a Cashfree return with the backend before showing success', async () => {
    const fixture = setup({ gateway: 'cashfree', order_id: 'oi_7_abc' });
    const req = http.expectOne('/api/payments/cashfree/verify');
    expect(req.request.body).toEqual({ order_id: 'oi_7_abc', payload: { order_id: 'oi_7_abc' } });
    req.flush({ status: 'paid', payment_id: 5, subscription_id: 1, message: '' });
    await fixture.whenStable();
    http.expectOne('/api/billing/me').flush(ME);
    http.expectOne('/api/plans').flush([]);
    expect(fixture.componentInstance.notice()).toContain('Payment confirmed');
  });
});

describe('public page routes', () => {
  it('registers the new informational and legal pages', () => {
    const paths = routes.map((r) => r.path);
    for (const p of ['features', 'how-it-works', 'trust', 'privacy-policy', 'terms', 'refund-policy', 'acceptable-use', 'billing']) {
      expect(paths).toContain(p);
    }
  });

  it('keeps the old /terms-and-conditions URL working', () => {
    const old = routes.find((r) => r.path === 'terms-and-conditions');
    expect(old?.redirectTo).toBe('terms');
  });

  it('protects /billing with the auth guard', () => {
    expect(routes.find((r) => r.path === 'billing')?.canActivate?.length).toBeGreaterThan(0);
  });
});
