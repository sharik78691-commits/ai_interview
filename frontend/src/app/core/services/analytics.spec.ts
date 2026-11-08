import { trackEvent } from './analytics';

describe('trackEvent', () => {
  const w = window as unknown as { gtag?: (...a: unknown[]) => void };
  let saved: typeof w.gtag;
  beforeEach(() => (saved = w.gtag));
  afterEach(() => (w.gtag = saved));

  it('forwards the event to gtag when it exists', () => {
    const spy = jasmine.createSpy('gtag');
    w.gtag = spy;
    trackEvent('sign_up', { method: 'email' });
    expect(spy).toHaveBeenCalledWith('event', 'sign_up', { method: 'email' });
  });

  it('is a no-op when gtag is missing or throws', () => {
    w.gtag = undefined;
    expect(() => trackEvent('sign_up')).not.toThrow();
    w.gtag = () => {
      throw new Error('blocked');
    };
    expect(() => trackEvent('sign_up')).not.toThrow();
  });
});
