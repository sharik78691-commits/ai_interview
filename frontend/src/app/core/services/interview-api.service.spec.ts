import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { API_BASE_URL } from '../api/api-config';
import { readCsrfToken } from '../auth/csrf';
import { InterviewApiService } from './interview-api.service';
import { ResumeService } from './resume.service';

describe('InterviewApiService', () => {
  let http: HttpTestingController;

  function setup(base?: string) {
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        ...(base !== undefined ? [{ provide: API_BASE_URL, useValue: base }] : []),
      ],
    });
    http = TestBed.inject(HttpTestingController);
  }

  afterEach(() => http.verify());

  it('posts prepare/analyze and reads health on the configured base URL', () => {
    setup('https://api.example.test');
    const api = TestBed.inject(InterviewApiService);

    api.prepare({ resumeText: 'r', jobDescription: 'j', responseLength: 'short' }).subscribe();
    const prep = http.expectOne('https://api.example.test/api/interview/prepare');
    expect(prep.request.method).toBe('POST');
    expect(prep.request.body).toEqual({ resumeText: 'r', jobDescription: 'j', responseLength: 'short' });
    prep.flush({ status: 'ready' });

    api.analyze({ question: 'q', resumeText: 'r', jobDescription: 'j', responseLength: 'short', model: 'm' })
      .subscribe();
    const an = http.expectOne('https://api.example.test/api/interview/analyze');
    expect(an.request.body.model).toBe('m');
    an.flush({ question: 'q', questionType: 'general', answerPoints: ['a'] });

    api.health().subscribe((h) => expect(h.status).toBe('ok'));
    http.expectOne('https://api.example.test/api/health').flush({ status: 'ok' });
  });

  it('defaults to the same-origin base in web builds', () => {
    setup();
    TestBed.inject(InterviewApiService).health().subscribe();
    http.expectOne('/api/health').flush({ status: 'ok' });
  });

  it('ResumeService delegates interview calls with its current resume/JD', () => {
    setup('');
    const resumes = TestBed.inject(ResumeService);
    const saved = [localStorage.getItem('aia_resumeText'), localStorage.getItem('aia_jd')];
    resumes.saveResumeText('My resume');
    resumes.saveJobDescription('The job');
    resumes.analyzeQuestion('Explain REST', 'long', 'groq').subscribe();
    const req = http.expectOne('/api/interview/analyze');
    expect(req.request.body).toEqual({
      question: 'Explain REST',
      resumeText: 'My resume',
      jobDescription: 'The job',
      responseLength: 'long',
      model: 'groq',
    });
    req.flush({ question: 'Explain REST', questionType: 'technical', answerPoints: ['x'] });
    // Restore whatever the browser had before this spec.
    saved[0] === null ? localStorage.removeItem('aia_resumeText') : localStorage.setItem('aia_resumeText', saved[0]);
    saved[1] === null ? localStorage.removeItem('aia_jd') : localStorage.setItem('aia_jd', saved[1]);
  });
});

describe('readCsrfToken', () => {
  it('extracts and decodes the aia_csrf cookie', () => {
    expect(readCsrfToken('a=1; aia_csrf=abc%2Bdef; b=2')).toBe('abc+def');
    expect(readCsrfToken('aia_csrf=first')).toBe('first');
  });

  it('returns empty when the cookie is absent or only a prefix matches', () => {
    expect(readCsrfToken('')).toBe('');
    expect(readCsrfToken('xaia_csrf=nope')).toBe('');
  });
});
