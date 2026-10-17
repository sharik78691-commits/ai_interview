import { HttpClient } from '@angular/common/http';
import { Injectable } from '@angular/core';
import { Observable, catchError, map, of, switchMap, tap } from 'rxjs';
import { AIInterviewResponse, ResumeData } from '../models/interview.models';
import { environment } from '../../../environments/environment';

// Relative base in web builds (dev-server proxy / same-origin), absolute URL in
// the Electron build where no proxy exists under file://.
const BASE = environment.apiBaseUrl;

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

/** One resume in the server-side library (list view — no heavy text). */
export interface StoredResume {
  id: number;
  filename: string;
  extension: string;
  size_bytes: number;
  chars: number;
  skills: string[];
  is_active: boolean;
  created_at: string | null;
  updated_at: string | null;
}

/** Full stored resume with parsed sections and editable source text. */
export interface StoredResumeDetail extends StoredResume {
  name: string;
  summary: string;
  experience: string[];
  projects: string[];
  education: string[];
  certifications: string[];
  raw_text: string;
}

@Injectable({ providedIn: 'root' })
export class ResumeService {
  constructor(private http: HttpClient) {}

  /** Id of the row created by the latest upload (null when unknown). */
  lastStoredId: number | null = null;

  get resumeText(): string {
    return localStorage.getItem('aia_resumeText') ?? '';
  }
  get jobDescription(): string {
    return localStorage.getItem('aia_jd') ?? '';
  }

  uploadResume(file: File): Observable<ResumeData> {
    const form = new FormData();
    form.append('file', file);
    this.lastStoredId = null;
    return this.http.post<{ filename: string; chars: number; resume: Record<string, unknown> }>(`${BASE}/api/resume/upload`, form).pipe(
      tap((d) => {
        const r = (d?.resume ?? {}) as Record<string, unknown>;
        const raw = (r['raw_text'] as string) ?? (r['rawText'] as string) ?? '';
        if (raw) localStorage.setItem('aia_resumeText', raw);
        localStorage.setItem('aia_resumeData', JSON.stringify({ ...r, rawText: raw }));
        const id = (d as unknown as { id?: unknown })?.id;
        this.lastStoredId = typeof id === 'number' ? id : null;
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

  /** Id of the stored row backing the editor (survives reloads). */
  get storedEditingId(): number | null {
    const v = localStorage.getItem('aia_editingResumeId');
    const n = v === null ? NaN : Number(v);
    return Number.isInteger(n) ? n : null;
  }

  saveEditingId(id: number | null): void {
    if (id === null) localStorage.removeItem('aia_editingResumeId');
    else localStorage.setItem('aia_editingResumeId', String(id));
  }

  /**
   * Remove all locally stored resume / job-description / demo data.
   * Called on logout so the next user on this browser starts clean.
   */
  clearStoredData(): void {
    localStorage.removeItem('aia_resumeText');
    localStorage.removeItem('aia_jd');
    localStorage.removeItem('aia_resumeData');
    localStorage.removeItem('aia_editingResumeId');
    localStorage.removeItem('aia_demo');
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

  analyzeQuestion(question: string, responseLength: string = 'medium', model?: string): Observable<AIInterviewResponse> {
    return this.http.post<AIInterviewResponse>(`${BASE}/api/interview/analyze`, {
      question,
      resumeText: this.resumeText,
      jobDescription: this.jobDescription,
      responseLength,
      model,
    });
  }

  /** Backend health, including whether meeting-audio transcription is available. */
  checkHealth(): Observable<HealthInfo> {
    return this.http.get<HealthInfo>(`${BASE}/api/health`);
  }

  // ------------------------------------------------- server resume library

  /** The signed-in user's stored resumes, newest first. Empty when signed out. */
  listResumes(): Observable<StoredResume[]> {
    return this.http.get<StoredResume[]>(`${BASE}/api/resumes`).pipe(
      catchError(() => of([])),
    );
  }

  /** Full stored resume with parsed sections and source text. */
  getResume(id: number): Observable<StoredResumeDetail> {
    return this.http.get<StoredResumeDetail>(`${BASE}/api/resumes/${id}`);
  }

  /** Make a stored resume the active one for interviews. */
  selectResume(id: number): Observable<StoredResumeDetail> {
    return this.http.patch<StoredResumeDetail>(`${BASE}/api/resumes/${id}`, {
      is_active: true,
    });
  }

  /** Delete a stored resume (the newest remaining one becomes active). */
  deleteResume(id: number): Observable<{ message: string }> {
    return this.http.delete<{ message: string }>(`${BASE}/api/resumes/${id}`);
  }

  /** Original-file download URL (session cookie travels on navigation). */
  downloadUrl(id: number): string {
    return `${BASE}/api/resumes/${id}/download`;
  }

  /**
   * Adopt the server's active resume into the local editor when the editor is
   * empty (fresh browser / new device / after logout). Never overwrites text
   * the user already has. Resolves the adopted detail, or null.
   */
  adoptActiveResume(): Observable<StoredResumeDetail | null> {
    if (this.resumeText.trim()) return of(null);
    return this.listResumes().pipe(
      map((rows) => rows.find((r) => r.is_active) ?? null),
      switchMap((row) => {
        if (!row) return of(null);
        return this.getResume(row.id).pipe(
          tap((d) => this.applyDetail(d)),
          catchError(() => of(null)),
        );
      }),
      catchError(() => of(null)),
    );
  }

  /** Store pasted/typed editor text as a resume (becomes the active one). */
  createFromText(filename: string, rawText: string): Observable<StoredResumeDetail> {
    return this.http.post<StoredResumeDetail>(`${BASE}/api/resumes/from-text`, {
      filename,
      raw_text: rawText,
    });
  }

  /** Save editor corrections onto a stored resume (re-parses skills). */
  updateResumeText(id: number, rawText: string): Observable<StoredResumeDetail> {
    return this.http.patch<StoredResumeDetail>(`${BASE}/api/resumes/${id}`, {
      raw_text: rawText,
    });
  }

  /** Push a stored resume's text + parsed data into the local editor. */
  applyDetail(d: StoredResumeDetail): void {
    this.saveResumeText(d.raw_text || '');
    localStorage.setItem(
      'aia_resumeData',
      JSON.stringify({
        name: d.name,
        summary: d.summary,
        skills: d.skills,
        experience: d.experience,
        projects: d.projects,
        education: d.education,
        certifications: d.certifications,
        rawText: d.raw_text,
      }),
    );
  }
}
