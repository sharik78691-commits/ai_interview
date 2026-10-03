import { HttpClient } from '@angular/common/http';
import { Injectable } from '@angular/core';
import { Observable, map, tap } from 'rxjs';
import { AIInterviewResponse, ResumeData } from '../models/interview.models';

const BASE = 'http://localhost:8000';

/** Shape returned by GET /api/health. */
export interface HealthInfo {
  status: string;
  model?: string;
  demo_mode?: boolean;
  llm_configured?: boolean;
  /** Server-side speech-to-text for interviewer meeting/tab audio. */
  stt_configured?: boolean;
  stt_model?: string;
}

@Injectable({ providedIn: 'root' })
export class ResumeService {
  constructor(private http: HttpClient) {}

  get resumeText(): string {
    return localStorage.getItem('aia_resumeText') ?? '';
  }
  get jobDescription(): string {
    return localStorage.getItem('aia_jd') ?? '';
  }

  uploadResume(file: File): Observable<ResumeData> {
    const form = new FormData();
    form.append('file', file);
    return this.http.post<{ filename: string; chars: number; resume: Record<string, unknown> }>(`${BASE}/api/resume/upload`, form).pipe(
      tap((d) => {
        const r = (d?.resume ?? {}) as Record<string, unknown>;
        const raw = (r['raw_text'] as string) ?? (r['rawText'] as string) ?? '';
        if (raw) localStorage.setItem('aia_resumeText', raw);
        localStorage.setItem('aia_resumeData', JSON.stringify({ ...r, rawText: raw }));
      }),
      // Map backend ResumeUploadResponse -> frontend ResumeData shape
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      map((d: any) => {
        const r = d?.resume ?? d;
        return {
          name: r.name ?? '',
          email: r.email ?? '',
          summary: r.summary ?? '',
          skills: r.skills ?? [],
          experience: r.experience ?? [],
          projects: r.projects ?? [],
          education: r.education ?? [],
          certifications: r.certifications ?? [],
          rawText: r.raw_text ?? r.rawText ?? '',
          raw_text: r.raw_text ?? r.rawText ?? '',
        } as ResumeData;
      }),
    );
  }

  get storedResumeData(): ResumeData | null {
    try {
      const raw = localStorage.getItem('aia_resumeData');
      return raw ? (JSON.parse(raw) as ResumeData) : null;
    } catch {
      return null;
    }
  }

  saveResumeText(text: string): void {
    localStorage.setItem('aia_resumeText', text);
  }

  saveJobDescription(jd: string): void {
    localStorage.setItem('aia_jd', jd);
  }

  prepareInterview(
    resumeText: string,
    jobDescription: string,
    responseLength: string = 'medium',
  ): Observable<{ status: string }> {
    this.saveResumeText(resumeText);
    this.saveJobDescription(jobDescription);
    return this.http.post<{ status: string }>(`${BASE}/api/interview/prepare`, {
      resumeText,
      jobDescription,
      responseLength,
    });
  }

  analyzeQuestion(question: string, responseLength: string = 'medium'): Observable<AIInterviewResponse> {
    return this.http.post<AIInterviewResponse>(`${BASE}/api/interview/analyze`, {
      question,
      resumeText: this.resumeText,
      jobDescription: this.jobDescription,
      responseLength,
    });
  }

  /** Backend health, including whether meeting-audio transcription is available. */
  checkHealth(): Observable<HealthInfo> {
    return this.http.get<HealthInfo>(`${BASE}/api/health`);
  }
}
