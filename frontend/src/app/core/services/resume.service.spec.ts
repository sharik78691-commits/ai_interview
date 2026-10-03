import { of } from 'rxjs';
import { ResumeService } from './resume.service';

describe('ResumeService', () => {
  it('persists resume text and jd to localStorage', () => {
    localStorage.clear();
    const httpMock = { post: () => of({ status: 'ok' }), get: () => of({ status: 'ok' }) };
    const svc = new ResumeService(httpMock as never);
    svc.saveResumeText('resume abc');
    svc.saveJobDescription('jd xyz');
    expect(svc.resumeText).toBe('resume abc');
    expect(svc.jobDescription).toBe('jd xyz');
  });

  it('returns stored resume data or null', () => {
    localStorage.clear();
    const httpMock = { post: () => of({}), get: () => of({}) };
    const svc = new ResumeService(httpMock as never);
    expect(svc.storedResumeData).toBeNull();
    localStorage.setItem('aia_resumeData', JSON.stringify({ skills: ['Go'], rawText: 'x' }));
    expect(svc.storedResumeData?.skills).toEqual(['Go']);
  });
});
