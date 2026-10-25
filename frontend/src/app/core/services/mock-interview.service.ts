import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { API_BASE_URL } from '../api/api-config';
import {
  MockFeedback,
  MockFeedbackRequest,
  MockQuestionsRequest,
  MockQuestionsResponse,
} from '../models/mock-interview.models';

/** HTTP client for mock interviews: practice questions, then feedback per answer. */
@Injectable({ providedIn: 'root' })
export class MockInterviewService {
  private readonly http = inject(HttpClient);
  private readonly base = inject(API_BASE_URL);

  questions(body: MockQuestionsRequest): Observable<MockQuestionsResponse> {
    return this.http.post<MockQuestionsResponse>(`${this.base}/api/mock-interview/questions`, body);
  }

  feedback(body: MockFeedbackRequest): Observable<MockFeedback> {
    return this.http.post<MockFeedback>(`${this.base}/api/mock-interview/feedback`, body);
  }
}
