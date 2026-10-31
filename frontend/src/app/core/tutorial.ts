/** The OyeInterview video tutorial on YouTube (website + desktop app walkthrough). */
export const TUTORIAL = {
  youtubeId: 'TUZaStbmdag',
  url: 'https://youtu.be/TUZaStbmdag',
  duration: '2:47',
  /** Our own thumbnail (public/), so the page loads nothing from YouTube until Play. */
  thumbnail: '/tutorial-thumbnail.jpg',
  /**
   * Chapter starts (seconds + clock label), rounded up from the video's real
   * chapter starts so a jump never opens on the previous chapter's last frame.
   * Keep in sync with the YouTube description chapters.
   */
  chapters: [
    { label: 'Set up', start: 16, at: '0:16' },
    { label: 'Live practice', start: 46, at: '0:46' },
    { label: 'Mock interview', start: 88, at: '1:28' },
    { label: 'Desktop app', start: 105, at: '1:45' },
    { label: 'Plans', start: 138, at: '2:18' },
  ],
};

/** Privacy-enhanced embed (no YouTube cookies until the viewer presses Play). */
export function tutorialEmbedUrl(start = 0): string {
  return `https://www.youtube-nocookie.com/embed/${TUTORIAL.youtubeId}?autoplay=1&rel=0&start=${start}`;
}
