import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { routes } from '../../../app.routes';
import { BUSINESS } from '../../../core/business-info';
import { SiteFooterComponent } from '../../../shared/components/site-footer/site-footer.component';
import { ContactComponent } from '../../contact/contact.component';
import { PrivacyComponent } from '../../legal/privacy/privacy.component';
import { ShippingComponent } from '../../legal/shipping/shipping.component';
import { TermsComponent } from '../../legal/terms/terms.component';
import { AboutComponent } from './about.component';

/** Pages and details payment gateways (Razorpay / Cashfree) check before approval. */
describe('Payment-gateway approval pages', () => {
  beforeEach(() => TestBed.configureTestingModule({ providers: [provideRouter([])] }));

  function text(cmp: unknown): string {
    const f = TestBed.createComponent(cmp as never);
    f.detectChanges();
    return (f.nativeElement as HTMLElement).textContent ?? '';
  }

  it('registers public /about and /shipping-policy routes (with aliases)', () => {
    const paths = routes.map((r) => r.path);
    expect(paths).toContain('about');
    expect(paths).toContain('about-us');
    expect(paths).toContain('shipping-policy');
    expect(paths).toContain('delivery-policy');
    const about = routes.find((r) => r.path === 'about')!;
    expect(about.canActivate).toBeUndefined(); // reviewers are not signed in
  });

  it('About us names the operator and describes practice & preparation', () => {
    const t = text(AboutComponent);
    expect(t).toContain(BUSINESS.operatorName);
    expect(t).toContain('Mohammed Raees Shaikh, India');
    expect(t).toContain('practice and preparation');
  });

  it('Shipping policy says digital service with instant activation', () => {
    const t = text(ShippingComponent);
    expect(t).toContain('no physical shipping');
    expect(t).toContain('activated automatically and instantly');
    expect(t).toContain(BUSINESS.email);
  });

  it('Contact shows the operator, email and the no-GST note — no phone or street address', () => {
    const t = text(ContactComponent);
    expect(t).toContain('Mohammed Raees Shaikh');
    expect(t).toContain(BUSINESS.email);
    expect(t).not.toMatch(/Phone|Address:/);
    expect(t).toContain('not registered under GST');
  });

  it('Terms and Privacy identify the operator; Terms carries the GST note', () => {
    const terms = text(TermsComponent);
    expect(terms).toContain(BUSINESS.operatorName);
    expect(terms).toContain('not registered under GST');
    expect(text(PrivacyComponent)).toContain(BUSINESS.operatorName);
  });

  it('footer links About and Shipping and shows operator details', () => {
    const f = TestBed.createComponent(SiteFooterComponent);
    f.detectChanges();
    const el = f.nativeElement as HTMLElement;
    const hrefs = Array.from(el.querySelectorAll('a')).map((a) => a.getAttribute('href'));
    expect(hrefs).toContain('/about');
    expect(hrefs).toContain('/shipping-policy');
    expect(el.querySelector('[data-testid="footer-operator"]')!.textContent).toContain(BUSINESS.operatorName);
  });
});
