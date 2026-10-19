import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { API_BASE_URL } from '../api/api-config';
import { AIInterviewResponse } from '../models/interview.models';

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

export interface PrepareInterviewRequest {
  resumeText: string;
  jobDescription: string;
  responseLength: string;
}

export interface AnalyzeQuestionRequest {
  question: string;
  resumeText: string;
  jobDescription: string;
  responseLength: string;
  model?: string;
}

/**
 * HTTP client for the interview endpoints (REST fallback for answers,
 * context preparation, backend health). Pure transport: no local state —
 * callers decide which resume/JD to send.
 */
@Injectable({ providedIn: 'root' })
export class InterviewApiService {
  private readonly http = inject(HttpClient);
  private readonly base = inject(API_BASE_URL);

  prepare(body: PrepareInterviewRequest): Observable<{ status: string }> {
    return this.http.post<{ status: string }>(`${this.base}/api/interview/prepare`, body);
  }

  analyze(body: AnalyzeQuestionRequest): Observable<AIInterviewResponse> {
    return this.http.post<AIInterviewResponse>(`${this.base}/api/interview/analyze`, body);
  }

  /** Backend health, including whether meeting-audio transcription is available. */
  health(): Observable<HealthInfo> {
    return this.http.get<HealthInfo>(`${this.base}/api/health`);
  }
}
