import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';
import { ResumeService } from './resume.service';

describe('ResumeService', () => {
  // The service resolves its collaborators (API base URL, InterviewApiService)
  // with inject(), so hand-built instances need an injection context.
  const make = (httpMock: object) => {
    TestBed.configureTestingModule({ providers: [provideHttpClient(), provideHttpClientTesting()] });
    return TestBed.runInInjectionContext(() => new ResumeService(httpMock as never));
  };

  it('persists resume text and jd to localStorage', () => {
    localStorage.clear();
    const httpMock = { post: () => of({ status: 'ok' }), get: () => of({ status: 'ok' }) };
    const svc = make(httpMock);
    svc.saveResumeText('resume abc');
    svc.saveJobDescription('jd xyz');
    expect(svc.resumeText).toBe('resume abc');
    expect(svc.jobDescription).toBe('jd xyz');
  });

  it('returns stored resume data or null', () => {
    localStorage.clear();
    const httpMock = { post: () => of({}), get: () => of({}) };
    const svc = make(httpMock);
    expect(svc.storedResumeData).toBeNull();
    localStorage.setItem('aia_resumeData', JSON.stringify({ skills: ['Go'], rawText: 'x' }));
    expect(svc.storedResumeData?.skills).toEqual(['Go']);
  });
});
