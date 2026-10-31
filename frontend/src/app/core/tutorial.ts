/** The OyeInterview video tutorial on YouTube (website + desktop app walkthrough). */
export const TUTORIAL = {
  youtubeId: 'TUZaStbmdag',
  url: 'https://youtu.be/TUZaStbmdag',
  duration: '2:47',
  /** Our own thumbnail (public/), so the page loads nothing from YouTube until Play. */
  thumbnail: '/tutorial-thumbnail.jpg',
  /** Chapter starts (seconds + clock label); keep in sync with the video's chapters. */
  chapters: [
    { label: 'Set up', start: 15, at: '0:15' },
    { label: 'Live practice', start: 45, at: '0:45' },
    { label: 'Mock interview', start: 87, at: '1:27' },
    { label: 'Desktop app', start: 104, at: '1:44' },
    { label: 'Plans', start: 137, at: '2:17' },
  ],
};

/** Privacy-enhanced embed (no YouTube cookies until the viewer presses Play). */
export function tutorialEmbedUrl(start = 0): string {
  return `https://www.youtube-nocookie.com/embed/${TUTORIAL.youtubeId}?autoplay=1&rel=0&start=${start}`;
}
