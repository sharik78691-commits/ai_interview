import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { API_BASE_URL } from '../api/api-config';
import { Gateway } from '../models/billing.models';
import { BillingService } from './billing.service';

describe('BillingService gateways', () => {
  beforeEach(() =>
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting(), { provide: API_BASE_URL, useValue: '' }],
    }),
  );

  // The admin picks the gateway on the server; the client shows what it returns
  // (known keys only) instead of hard-coding one gateway.
  it('shows whichever known gateway the server offers, Razorpay included', () => {
    let keys: string[] = [];
    TestBed.inject(BillingService).gateways().subscribe((g) => (keys = g.map((x) => x.key)));
    TestBed.inject(HttpTestingController)
      .expectOne('/api/payments/gateways')
      .flush([
        { key: 'razorpay', display_name: 'Razorpay', currencies: ['INR'] },
        { key: 'paypal', display_name: 'Unknown', currencies: ['USD'] },
      ] as unknown as Gateway[]);
    expect(keys).toEqual(['razorpay']);
  });

  it('reads the site-wide payment status', () => {
    let available: boolean | undefined;
    TestBed.inject(BillingService).paymentStatus().subscribe((s) => (available = s.available));
    TestBed.inject(HttpTestingController)
      .expectOne('/api/payments/status')
      .flush({ payments_enabled: false, active_gateway: 'cashfree', active_gateway_name: '', available: false, message: 'x' });
    expect(available).toBe(false);
  });
});
