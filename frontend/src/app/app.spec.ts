import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { App } from './app';
import { AuthService } from './core/auth/auth.service';
import { InterviewWsService } from './core/services/interview-ws.service';

describe('App', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [App],
      providers: [provideHttpClient(), provideHttpClientTesting(), provideRouter([])],
    }).compileComponents();
  });

  it('should create the app', () => {
    const fixture = TestBed.createComponent(App);
    const app = fixture.componentInstance;
    expect(app).toBeTruthy();
  });

  it('should render navbar', () => {
    const fixture = TestBed.createComponent(App);
    fixture.detectChanges();
    const compiled = fixture.nativeElement as HTMLElement;
    expect(compiled.querySelector('app-navbar')).toBeTruthy();
  });

  it('clears interview data and redirects after sign-out', () => {
    localStorage.setItem('aia_resumeText', 'private resume');
    localStorage.setItem('aia_jd', 'private job');
    const ws = TestBed.inject(InterviewWsService);
    ws.history$.next([{ question: 'q', guidance: {} as never, timestamp: new Date() }]);
    const router = TestBed.inject(Router);
    const navigate = spyOn(router, 'navigate').and.resolveTo(true);
    const httpMock = TestBed.inject(HttpTestingController);

    const fixture = TestBed.createComponent(App);
    fixture.componentInstance.ngOnInit();
    httpMock.match(() => true).forEach((req) => req.flush({}));

    TestBed.inject(AuthService).logout().subscribe();
    httpMock.expectOne('/api/auth/logout').flush({ message: 'Signed out.' });

    expect(localStorage.getItem('aia_resumeText')).toBeNull();
    expect(localStorage.getItem('aia_jd')).toBeNull();
    expect(ws.history$.value).toEqual([]);
    expect(navigate).toHaveBeenCalledWith(['/login'], { replaceUrl: true });
  });
});
