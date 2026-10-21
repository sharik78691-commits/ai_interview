/** Shared types for the desktop-only screenshot analysis feature. */

export interface ScreenshotLimits {
  maxImages: number;
  maxImageBytes: number;
  maxTotalBytes: number;
  minDimension: number;
  maxDimension: number;
}

/** One selectable model (non-secret metadata only). */
export interface ScreenshotModel {
  key: string;
  provider: string;
  displayName: string;
  limits: ScreenshotLimits;
}

export interface CapturedImage {
  mimeType: string;
  /** Base64, no data-URL prefix. */
  data: string;
  width: number;
  height: number;
  sourceName: string;
}

export interface CaptureSource {
  id: string;
  name: string;
  kind: 'screen' | 'window';
  /** Small preview data-URL for the picker. */
  thumbnail: string;
}

export interface FollowUp {
  question: string;
  suggestedResponse: string;
}

export interface CodeAnalysis {
  explanation: string;
  complexity: string;
  edgeCases: string[];
  improvedAnswer: string;
}

export interface AnalysisResult {
  questions: string[];
  topic: string;
  seniority: string;
  directAnswer: string;
  interviewAnswer: string;
  keyPoints: string[];
  followUps: FollowUp[];
  code: CodeAnalysis | null;
}

/**
 * Screenshot credits from the backend (1 credit = 1 screenshot). `remaining`
 * is on the plan that will be charged next; `totalRemaining` across live plans.
 */
export interface ScreenshotUsage {
  planName: string | null;
  allowance: number;
  used: number;
  remaining: number;
  totalRemaining: number;
  expiresAt: string | null;
  /** Admin account: analyses are never charged (credit counts don't apply). */
  unlimited?: boolean;
}

/** Error codes that mean "buy or renew a plan" (HTTP 402 from the backend). */
export const PLAN_ERROR_CODES: readonly string[] = ['NO_CREDITS', 'SUBSCRIPTION_REQUIRED'];

export interface HistoryEntry {
  id: string;
  createdAt: number;
  model: string;
  imageCount: number;
  result: AnalysisResult;
}

/** Error surfaced to the UI; `code` mirrors the main-process error codes. */
export class ScreenshotError extends Error {
  constructor(
    readonly code: string,
    message: string,
  ) {
    super(message);
    this.name = 'ScreenshotError';
  }
}
