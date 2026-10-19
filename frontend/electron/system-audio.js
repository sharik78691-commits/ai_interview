// "Auto Select Meeting Audio" for the desktop app: Windows system audio with
// NO picker and NO Stereo Mix / VB-Cable.
//
// Electron lets the main process answer the page's getDisplayMedia() call
// itself. We hand back the primary screen plus `audio: 'loopback'`, which is
// Chromium's WASAPI loopback of everything Windows plays (the meeting audio).
// The renderer drops the video track at once and keeps only the audio.

/**
 * Build the handler for `session.setDisplayMediaRequestHandler`.
 * Requests that don't ask for audio are denied (the screenshot feature uses
 * desktopCapturer over IPC, not getDisplayMedia).
 */
function createLoopbackAudioHandler({ desktopCapturer, platform = process.platform }) {
  return (request, callback) => {
    if (!request || !request.audioRequested) {
      callback({});
      return;
    }
    desktopCapturer
      .getSources({ types: ['screen'], thumbnailSize: { width: 0, height: 0 } })
      .then((sources) => {
        const screenSource = sources && sources[0];
        if (!screenSource) {
          callback({});
          return;
        }
        // Loopback audio is supported on Windows; elsewhere grant video only so
        // the renderer sees "no audio track" and falls back to a loopback device.
        callback(platform === 'win32' ? { video: screenSource, audio: 'loopback' } : { video: screenSource });
      })
      .catch(() => callback({}));
  };
}

module.exports = { createLoopbackAudioHandler };
