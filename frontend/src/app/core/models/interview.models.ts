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

export type ResponseLength = 'short' | 'medium' | 'long';

export interface AIInterviewResponse {
  question: string;
  questionType: string;
  answerPoints: string[];
  star?: string | StarBreakdown | null;
  codeHint?: string | null;
  example?: string | null;
  keyTakeaways?: string[];
  followUpQuestions: string[];
  responseLength?: ResponseLength;
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
}

export const RESPONSE_LENGTH_LABELS: Record<ResponseLength, string> = {
  short: 'Short — quick bullets',
  medium: 'Medium — structured answer',
  long: 'Long — detailed + examples',
};
