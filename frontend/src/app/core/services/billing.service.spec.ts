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

  // Checkout offers only Cashfree for now, even though the backend still has Razorpay.
  it('hides Razorpay at checkout and keeps Cashfree', () => {
    let keys: string[] = [];
    TestBed.inject(BillingService).gateways().subscribe((g) => (keys = g.map((x) => x.key)));
    TestBed.inject(HttpTestingController)
      .expectOne('/api/payments/gateways')
      .flush([
        { key: 'razorpay', display_name: 'Razorpay', currencies: ['INR'] },
        { key: 'cashfree', display_name: 'Cashfree', currencies: ['INR'] },
      ] as unknown as Gateway[]);
    expect(keys).toEqual(['cashfree']);
  });
});
