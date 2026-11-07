/**
 * Body copy for the keyword landing pages. Lazy-loaded with
 * SeoLandingComponent so it stays out of the initial bundle; titles,
 * descriptions and labels live in seo-pages.ts (needed eagerly by routes).
 * Copy must stay unique per page and only describe features that exist today.
 */
export interface SeoSection {
  heading: string;
  paragraphs: string[];
  bullets?: string[];
}

export interface SeoFaq {
  q: string;
  a: string;
}

export interface SeoPageContent {
  slug: string;
  h1: string;
  lead: string;
  sections: SeoSection[];
  faq: SeoFaq[];
}

export const SEO_CONTENT: Record<string, SeoPageContent> = {
  'ai-interview': {
    slug: 'ai-interview',
    h1: 'AI Interview: the complete guide to preparing, practising and passing',
    lead:
      '“AI interview” can mean two things: an interview where the company uses AI to ask or score your answers, or an AI tool that helps you prepare for any interview. This guide covers both — how AI interviews work, how to prepare for one, and how to practise with OyeInterview, an AI interview assistant built around your own resume.',
    sections: [
      {
        heading: 'What is an AI interview?',
        paragraphs: [
          'More and more companies use AI in hiring. In an AI-led interview you may answer recorded video questions, speak to a virtual interviewer, or take an online assessment where software transcribes and scores what you say. Recruiters then review the shortlisted candidates.',
          'On the other side, candidates now use AI interview tools to prepare: AI mock interviews, answer outlines, STAR stories and coding hints. Both trends mean the same thing for you — clear, structured, relevant answers matter more than ever.',
        ],
      },
      {
        heading: 'How AI interviews evaluate your answers',
        paragraphs: ['Every platform is different, but most AI interview systems look at a similar set of signals:'],
        bullets: [
          'Relevance — does your answer actually address the question and the job description?',
          'Structure — is there a clear beginning, an example and a result (the STAR method works well)?',
          'Skills and keywords — do you mention the tools, technologies and competencies the role needs?',
          'Clarity — do you speak at a steady pace, without long pauses or filler words?',
          'Consistency — do your examples match your resume and earlier answers?',
        ],
      },
      {
        heading: 'How to prepare for an AI interview: 7 practical tips',
        paragraphs: [],
        bullets: [
          'Read the job description and list the 5–6 skills it repeats most. Plan one example for each.',
          'Prepare a 60–90 second “Tell me about yourself” that connects your background to this role.',
          'Write 4–5 STAR stories: a challenge, a conflict, a failure, a leadership moment and an achievement with numbers.',
          'Practise out loud, not just in your head — AI scoring depends on how you speak.',
          'Keep answers between 1 and 2 minutes; get to the point in the first sentence.',
          'Check your setup: quiet room, good light, stable internet, camera at eye level.',
          'Do at least one full mock interview with feedback before the real one.',
        ],
      },
      {
        heading: 'Common AI interview questions',
        paragraphs: [
          'Tell me about yourself. Why do you want this role? Describe a time you solved a difficult problem. Tell me about a conflict with a teammate. What is your biggest weakness? Explain a project you are proud of. Where do you see yourself in five years? For technical roles, expect concept questions, short coding problems and “how would you design…” questions.',
        ],
      },
      {
        heading: 'Practise your AI interview with OyeInterview',
        paragraphs: ['OyeInterview is an AI interview assistant that turns your resume and the job description into answers that sound like you:'],
        bullets: [
          'AI mock interviews for any role — technical, behavioral or HR — with a 1–10 score and specific feedback.',
          'Resume-aware answer points and STAR outlines for every question.',
          'Coding hints, code skeletons and time/space complexity notes.',
          'Live transcription of practice audio, or type any question.',
          'Simple prepaid plans with no auto-renewal.',
        ],
      },
    ],
    faq: [
      { q: 'What is an AI interview?', a: 'An interview where AI asks the questions, records or scores your answers — or, from the candidate side, an AI tool that helps you prepare and practise.' },
      { q: 'How do I pass an AI interview?', a: 'Match your answers to the job description, use the STAR method, speak clearly, keep answers to 1–2 minutes and practise out loud with a mock interview first.' },
      { q: 'Can I practise an AI interview for free?', a: 'Yes. Sign up for OyeInterview and try the demo for free; full practice sessions are part of the prepaid plans.' },
      { q: 'Is an AI interview harder than a normal interview?', a: 'Not harder, just different: there is less small talk and more focus on structure, relevance and clarity — all of which improve quickly with practice.' },
    ],
  },
  'ai-interview-assistant': {
    slug: 'ai-interview-assistant',
    h1: 'AI Interview Assistant that knows your resume',
    lead:
      'OyeInterview is an AI interview assistant built for job seekers. It listens for the question, reads your resume and the job description, and gives you short, structured answer points in seconds — for technical, behavioral, HR and coding rounds.',
    sections: [
      {
        heading: 'What is an AI interview assistant?',
        paragraphs: [
          'An AI interview assistant is software that helps you answer interview questions using artificial intelligence. Instead of generic answers copied from the internet, a good assistant uses your own experience — your resume, projects and the role you are applying for — so the answer sounds like you.',
          'OyeInterview does exactly that. You upload your resume once, save the job titles you are targeting, and every answer is written around your real background.',
        ],
      },
      {
        heading: 'Why candidates use OyeInterview as their interview assistant',
        paragraphs: ['Most people do not fail interviews because they lack skill. They freeze, ramble or forget their best examples. OyeInterview fixes that:'],
        bullets: [
          'Answer points appear first, so you know where to start within seconds.',
          'Behavioral questions get a STAR outline (Situation, Task, Action, Result) built from your resume.',
          'Coding questions get hints, an approach, a code skeleton and time/space complexity notes.',
          'Follow-ups like “why?” or “give an example” are answered in context of the previous question.',
          'Switch answer length between short, medium and long at any time.',
        ],
      },
      {
        heading: 'Works for every kind of interview',
        paragraphs: [
          'Software engineering, data, product, sales, support, finance, freshers and experienced professionals — the assistant adapts to the role and the job description you select. Use it for campus placements, HR rounds, technical rounds, system design discussions and managerial rounds.',
        ],
      },
      {
        heading: 'How to start in under 5 minutes',
        paragraphs: [
          'Create a free account, upload your resume (PDF, DOCX or TXT), add your target role and job description, then start a practice session or mock interview. Share the interview audio or type the question — the answer appears in seconds.',
        ],
      },
    ],
    faq: [
      { q: 'Is OyeInterview a free AI interview assistant?', a: 'You can sign up and try the demo for free. Full sessions use simple prepaid plans with no auto-renewal — see the Pricing page.' },
      { q: 'Does the AI interview assistant use my resume?', a: 'Yes. Your active resume and target job description are used to tailor every answer, so examples and skills come from your real experience.' },
      { q: 'Which interviews does it support?', a: 'Technical, behavioral, HR and coding interviews for any role — from freshers to senior professionals.' },
    ],
  },

  'ai-interview-copilot': {
    slug: 'ai-interview-copilot',
    h1: 'Your real-time AI interview copilot',
    lead:
      'Think of OyeInterview as a copilot sitting next to you during interview practice. It hears the question, understands what is being asked and suggests the key points to cover — in real time, while you keep talking in your own words.',
    sections: [
      {
        heading: 'What an interview copilot does',
        paragraphs: [
          'A pilot flies the plane; the copilot watches the instruments and calls out what matters. An AI interview copilot works the same way: you stay in control of the conversation, and the AI surfaces the points, examples and structure you might otherwise forget under pressure.',
        ],
      },
      {
        heading: 'Real-time features',
        paragraphs: ['OyeInterview is built for low latency so the help arrives while the question is still fresh:'],
        bullets: [
          'Live transcription of the shared interview audio, with automatic question detection.',
          'Your own microphone is transcribed separately, so your answers are never mistaken for questions.',
          'Short answer points stream first; the full structured answer follows.',
          'Type or paste a question any time audio is not available.',
          'Desktop app with screenshot analysis — capture a coding problem on screen and get an approach.',
        ],
      },
      {
        heading: 'Copilot vs. memorised answers',
        paragraphs: [
          'Memorised answers break the moment the interviewer rephrases a question. A copilot understands the actual question and builds the answer from your resume and the job description, so it still fits when the wording changes or a follow-up comes in.',
        ],
      },
      {
        heading: 'Use it responsibly',
        paragraphs: [
          'OyeInterview is designed for practice, preparation and mock interviews. Always follow the rules of the interview process you are in — see our Acceptable Use policy.',
        ],
      },
    ],
    faq: [
      { q: 'How fast does the AI interview copilot respond?', a: 'Answer points usually begin appearing within seconds of the question being detected, followed by the full structured answer.' },
      { q: 'Do I need a microphone?', a: 'No. You can share tab or meeting audio, use your microphone, or simply type the question.' },
      { q: 'Which browser should I use?', a: 'Chrome or Edge give the best experience because they support built-in speech recognition. A desktop app is also available.' },
    ],
  },

  'ai-mock-interview': {
    slug: 'ai-mock-interview',
    h1: 'AI mock interview with instant, honest feedback',
    lead:
      'Practise with an AI interviewer before the real one. Pick your role, interview type and difficulty, answer in your own words and get scored feedback on every answer — as many times as you need.',
    sections: [
      {
        heading: 'How the AI mock interview works',
        paragraphs: ['A mock interview on OyeInterview takes a few clicks to set up:'],
        bullets: [
          'Choose a job role and the interview type: technical, behavioral or HR.',
          'Pick a difficulty level that matches the company you are targeting.',
          'Answer each question by speaking or typing.',
          'Get a score out of 10, what worked, what to improve and what a strong answer would include.',
          'Retry any question until your answer feels natural.',
        ],
      },
      {
        heading: 'Why practise with AI instead of alone?',
        paragraphs: [
          'Practising in front of a mirror tells you nothing about the quality of your answer. A friend may be too polite. An AI mock interviewer gives consistent, specific feedback every time — and it is available at 2 AM the night before your interview.',
        ],
      },
      {
        heading: 'Great for freshers and experienced candidates',
        paragraphs: [
          'Freshers can rehearse “Tell me about yourself”, project explanations and HR questions. Experienced professionals can drill leadership, conflict and system-design style discussions with STAR-structured feedback.',
        ],
      },
    ],
    faq: [
      { q: 'Is the AI mock interview free?', a: 'You can try it after signing up. Unlimited practice is part of the prepaid plans listed on the Pricing page.' },
      { q: 'Can I answer by voice?', a: 'Yes. Speak your answer (Chrome or Edge recommended) or type it — both are scored the same way.' },
      { q: 'What roles are supported?', a: 'Any role you type in — software, data, product, sales, HR, finance, support and more.' },
    ],
  },

  'ai-interview-preparation': {
    slug: 'ai-interview-preparation',
    h1: 'AI interview preparation, built around your resume',
    lead:
      'Generic interview question lists do not know who you are. OyeInterview prepares you for your interview — using your resume, your projects and the job description of the role you actually applied for.',
    sections: [
      {
        heading: 'A smarter interview prep plan',
        paragraphs: ['Use OyeInterview as a step-by-step preparation routine:'],
        bullets: [
          'Day 1: Upload your resume and add the job description. Fix anything the parser missed.',
          'Day 2: Practise “Tell me about yourself”, strengths, weaknesses and “Why should we hire you?”.',
          'Day 3: Run technical or coding questions for your role and study the hints and complexity notes.',
          'Day 4: Take a full AI mock interview and review your scores.',
          'Interview day: a short warm-up session to get your answers flowing.',
        ],
      },
      {
        heading: 'Prepare for the questions you will actually get',
        paragraphs: [
          'Because OyeInterview reads the job description, the answers and follow-ups focus on the skills the employer listed — not on random questions from a blog post. Likely follow-up questions are suggested with every answer so you are never caught off guard.',
        ],
      },
      {
        heading: 'Technical, behavioral and HR — all in one place',
        paragraphs: [
          'Behavioral answers come in STAR format. Technical answers include examples and key takeaways. Coding answers include an approach, a skeleton and complexity analysis. HR answers stay short, positive and honest.',
        ],
      },
    ],
    faq: [
      { q: 'How long should I prepare for an interview with AI?', a: 'Even 30–60 minutes a day for 3–4 days makes a visible difference. Focus on your own projects and the job description.' },
      { q: 'Can AI help with HR interview questions?', a: 'Yes. OyeInterview builds HR answers such as “Why should we hire you?” and salary or notice-period questions from your profile.' },
      { q: 'Does it help with coding interviews?', a: 'Yes. You get hints, an approach, code skeletons and time/space complexity notes so you understand the solution.' },
    ],
  },

  'ai-interview-answer-generator': {
    slug: 'ai-interview-answer-generator',
    h1: 'AI interview answer generator for any question',
    lead:
      'Type or paste any interview question and OyeInterview generates a clear, structured answer using your resume and the job you want — key points first, then the full answer, examples and likely follow-ups.',
    sections: [
      {
        heading: 'Answers that sound like you, not like a template',
        paragraphs: [
          'Most answer generators give the same paragraph to everyone. OyeInterview uses your actual projects, skills and achievements from your resume, so the answer is specific and believable — and easy for you to say in your own words.',
        ],
      },
      {
        heading: 'What every generated answer includes',
        paragraphs: ['Each answer is structured so you can scan it in seconds:'],
        bullets: [
          'Short answer points to open with.',
          'STAR outline for behavioral questions (Situation, Task, Action, Result).',
          'Real-world examples and key takeaways.',
          'Code hints and complexity notes for programming questions.',
          'Likely follow-up questions so you can prepare the next step.',
        ],
      },
      {
        heading: 'Popular questions people generate answers for',
        paragraphs: [
          '“Tell me about yourself”, “Why should we hire you?”, “What is your biggest weakness?”, “Describe a conflict with a teammate”, “Where do you see yourself in five years?”, “Explain your final-year project” and hundreds of role-specific technical questions.',
        ],
      },
    ],
    faq: [
      { q: 'Can I control the answer length?', a: 'Yes — switch between short, medium and long answers at any time.' },
      { q: 'Are AI-generated answers always correct?', a: 'No AI is perfect. Treat answers as a strong starting point and check facts before relying on them.' },
      { q: 'Does it support Indian job interviews?', a: 'Yes. It works for campus placements, IT services, product companies and startups, and answers in clear English.' },
    ],
  },

  'ai-coding-interview-assistant': {
    slug: 'ai-coding-interview-assistant',
    h1: 'AI coding interview assistant for DSA and technical rounds',
    lead:
      'Stuck on a data-structures or algorithms question? OyeInterview explains the approach, gives a code skeleton and breaks down time and space complexity — so you understand the solution, not just copy it.',
    sections: [
      {
        heading: 'Built for DSA, system design and technical questions',
        paragraphs: [
          'Arrays, strings, linked lists, trees, graphs, dynamic programming, SQL, OOP concepts and system-design discussions — the assistant adapts to the language and stack on your resume.',
        ],
      },
      {
        heading: 'What you get for each coding question',
        paragraphs: [],
        bullets: [
          'A plain-English approach, starting from brute force and moving to the optimal idea.',
          'A code skeleton you can complete and explain yourself.',
          'Time and space complexity with a one-line justification.',
          'Edge cases and likely follow-up questions from the interviewer.',
        ],
      },
      {
        heading: 'Screenshot analysis (desktop app)',
        paragraphs: [
          'In the OyeInterview desktop app you can capture a coding problem shown on your screen and get an approach and explanation in seconds — no retyping long problem statements.',
        ],
      },
    ],
    faq: [
      { q: 'Which programming languages are supported?', a: 'Common interview languages such as Java, Python, C++, JavaScript and SQL. The assistant follows the stack on your resume.' },
      { q: 'Will it write the full code for me?', a: 'It focuses on hints, approach, skeletons and complexity so you can write and explain the final solution yourself.' },
      { q: 'Is screenshot analysis available in the browser?', a: 'Screenshot analysis is a desktop-app feature. Typed and audio questions work in the browser.' },
    ],
  },
};
