export type InterviewStatus = 'idle' | 'listening' | 'processing' | 'thinking' | 'ready' | 'paused' | 'error';

export interface ResumeData {
  name?: string;
  email?: string;
  summary?: string;
  skills: string[];
  experience: string[];
  projects?: string[];
  education?: string[];
  certifications?: string[];
  rawText: string;
  raw_text?: string;
}

export interface StarBreakdown {
  situation?: string;
  task?: string;
  action?: string;
  result?: string;
  [k: string]: unknown;
}

export type ResponseLength = 'default' | 'short';

/** Legacy side-by-side table (older stored answers only; no longer rendered). */
export interface ComparisonTable {
  headers: string[];
  rows: string[][];
}

/** A likely interviewer follow-up with a short, strong answer. */
export interface FollowUpItem {
  question: string;
  answer?: string;
}

/**
 * One adaptive "Interview Answer Card". `answerPoints` is the spoken Best
 * Answer (one sentence per item); every other section is optional and only
 * present when it helps this question.
 */
export interface AIInterviewResponse {
  question: string;
  questionType: string;
  answerPoints: string[];
  simpleExplanation?: string | null;
  keyDetails?: string[];
  star?: string | StarBreakdown | null;
  approach?: string | null;
  /** Coding program 1: the polished AI solution. */
  codeHint?: string | null;
  /** Coding program 2: same solution, written the way a person types it. */
  humanCode?: string | null;
  complexity?: string | null;
  edgeCases?: string[];
  example?: string | null;
  followUps?: FollowUpItem[];
  /** Legacy fields — still accepted from older payloads. */
  keyTakeaways?: string[];
  followUpQuestions: string[];
  responseLength?: ResponseLength;
  comparison?: ComparisonTable | null;
}

export interface TranscriptEntry {
  speaker: 'interviewer' | 'candidate' | 'system';
  text: string;
  timestamp: Date;
  isQuestion: boolean;
}

export interface HistoryItem {
  question: string;
  guidance: AIInterviewResponse;
  timestamp: Date;
}

export interface AppSettings {
  aiModel: string;
  sttProvider: string;
  responseLength: ResponseLength;
  theme: string;
  micId: string;
  /** AI Guidance text size in px (user-adjustable, persisted). */
  guidanceFontSize: number;
  /** Site background color preset (see BACKGROUND_OPTIONS). */
  background: string;
  /** Background-default version the saved `background` was chosen under. */
  bgVersion?: number;
}

export const RESPONSE_LENGTH_LABELS: Record<ResponseLength, string> = {
  default: 'Default — covers everything the interviewer expects',
  short: 'Short — quick bullets',
};
