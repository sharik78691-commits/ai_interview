/**
 * Keyword landing pages ("AI interview assistant", "AI interview copilot", ...).
 *
 * Only the head metadata lives here (imported eagerly by routes and footer);
 * the body copy is in seo-content.ts, lazy-loaded with the page component.
 *
 * Adding a page: add an entry here and in seo-content.ts, and a <url> in
 * public/sitemap.xml. Routes and prerendering pick it up automatically.
 */
export interface SeoPageMeta {
  slug: string;
  /** <title> — primary keyword first, under ~65 characters. */
  title: string;
  /** Meta description — primary + secondary keyword, under 160 characters. */
  description: string;
  /** Short label used in breadcrumbs, footer and "related" links. */
  label: string;
}

export const SEO_PAGES: SeoPageMeta[] = [
  {
    slug: 'ai-interview',
    title: 'AI Interview — How It Works, Tips & Free Practice | OyeInterview',
    description:
      'What is an AI interview, how AI interviews work, how to prepare and pass one, and how to practise free with an AI interview assistant built on your resume.',
    label: 'AI interview guide',
  },
  {
    slug: 'ai-interview-assistant',
    title: 'AI Interview Assistant — Resume-Aware Answers | OyeInterview',
    description:
      'OyeInterview is an AI interview assistant that turns your resume and the job description into clear, confident answers for technical, HR and coding interviews.',
    label: 'AI interview assistant',
  },
  {
    slug: 'ai-interview-copilot',
    title: 'AI Interview Copilot — Real-Time Answer Help | OyeInterview',
    description:
      'AI interview copilot that transcribes the question and suggests resume-based answer points in seconds. Real-time help for technical, HR and coding interviews.',
    label: 'AI interview copilot',
  },
  {
    slug: 'ai-mock-interview',
    title: 'AI Mock Interview — Practice With Scored Feedback | OyeInterview',
    description:
      'Practice an AI mock interview for any role. Answer by voice or text and get a 1–10 score, what worked, what to improve and what a strong answer covers.',
    label: 'AI mock interview',
  },
  {
    slug: 'ai-interview-preparation',
    title: 'AI Interview Preparation — Prepare Smarter | OyeInterview',
    description:
      'AI interview preparation built around your resume and job description: likely questions, STAR answers, code hints and mock interviews with feedback.',
    label: 'AI interview preparation',
  },
  {
    slug: 'ai-interview-answer-generator',
    title: 'AI Interview Answer Generator — STAR Answers | OyeInterview',
    description:
      'Generate strong interview answers with AI. Type any interview question and get STAR answers, key points and examples tailored to your resume and target job.',
    label: 'AI interview answer generator',
  },
  {
    slug: 'ai-coding-interview-assistant',
    title: 'AI Coding Interview Assistant — Hints & Complexity | OyeInterview',
    description:
      'AI coding interview assistant with approach hints, code skeletons and time/space complexity notes, plus screenshot analysis in the desktop app.',
    label: 'AI coding interview assistant',
  },
];

export const SEO_PAGE_SLUGS = SEO_PAGES.map((p) => p.slug);
