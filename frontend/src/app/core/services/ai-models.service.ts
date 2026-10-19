import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable, catchError, map, of } from 'rxjs';
import { AiModel, AiModelsResponse } from '../models/ai-models';
import { API_BASE_URL } from '../api/api-config';

/**
 * Fetches the server-side AI model allowlist. Model IDs and endpoints live on
 * the backend only — this service just reads names + availability.
 */
@Injectable({ providedIn: 'root' })
export class AiModelsService {
  private http = inject(HttpClient);
  private readonly base = inject(API_BASE_URL);

  /** All configured models (empty array when the backend is unreachable). */
  listModels(): Observable<AiModel[]> {
    return this.http.get<AiModelsResponse>(`${this.base}/api/ai/models`).pipe(
      map((res) => res.models ?? []),
      catchError(() => of([])),
    );
  }
}
