/**
 * Browser-side audio conversion helpers.
 *
 * Why this exists
 * ---------------
 * There are two ways to get a clip into a form a transcription API accepts:
 *
 * 1. **Direct PCM capture** (preferred): pull samples straight off the shared
 *    stream with a ScriptProcessor and encode them here. No container is
 *    involved at all, so there is nothing for the provider to reject.
 * 2. **Decoder fallback**: `MediaRecorder` bytes re-encoded after
 *    `decodeAudioData`. Kept for browsers where the script processor is
 *    unavailable.
 *
 * Both paths end as 16 kHz mono PCM WAV — seekable, universally supported, and
 * exactly the rate Whisper uses internally.
 *
 * Pure functions, no Angular: straightforward to unit test.
 */

/** Whisper's native sampling rate. */
export const WAV_RATE = 16_000;

/**
 * Decode any browser-supported audio clip and re-encode it as 16 kHz mono WAV.
 * Returns null when the browser cannot decode the input, so the caller can fall
 * back to sending the original bytes rather than dropping the question.
 */
export async function toWav(data: ArrayBuffer): Promise<ArrayBuffer | null> {
  const Ctor =
    window.AudioContext ??
    (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  if (!Ctor) return null;
  let ctx: AudioContext | null = null;
  try {
    ctx = new Ctor();
    // decodeAudioData detaches the buffer it receives, so hand it a copy.
    const decoded = await ctx.decodeAudioData(data.slice(0));
    if (!decoded.length) return null;
    const channels = Math.max(1, decoded.numberOfChannels);
    const mono = new Float32Array(decoded.length);
    for (let c = 0; c < channels; c++) {
      const data = decoded.getChannelData(c);
      for (let i = 0; i < mono.length; i++) mono[i] += data[i];
    }
    if (channels > 1) for (let i = 0; i < mono.length; i++) mono[i] /= channels;
    return samplesToWav(mono, decoded.sampleRate, WAV_RATE);
  } catch {
    return null;
  } finally {
    // Release the audio hardware promptly; browsers limit live contexts.
    void ctx?.close().catch(() => undefined);
  }
}

/**
 * Concatenate raw PCM chunks captured from the shared stream and encode them
 * directly as WAV. This is the primary path: it never touches a container.
 */
export function pcmToWav(chunks: Float32Array[], sourceRate: number, targetRate = WAV_RATE): ArrayBuffer {
  if (!chunks.length || !sourceRate) return new ArrayBuffer(0);
  let total = 0;
  for (const chunk of chunks) total += chunk.length;
  if (!total) return new ArrayBuffer(0);

  const merged = new Float32Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    merged.set(chunk, offset);
    offset += chunk.length;
  }
  return samplesToWav(merged, sourceRate, targetRate);
}

/** True when a byte buffer really is a RIFF/WAVE file. */
export function isWav(data: ArrayBuffer | Uint8Array): boolean {
  const bytes = data instanceof Uint8Array ? data : new Uint8Array(data);
  return (
    bytes.length >= 44 &&
    bytes[0] === 0x52 && // R
    bytes[1] === 0x49 && // I
    bytes[2] === 0x46 && // F
    bytes[3] === 0x46 && // F
    bytes[8] === 0x57 && // W
    bytes[9] === 0x41 && // A
    bytes[10] === 0x56 && // V
    bytes[11] === 0x45 // E
  );
}

/** Resample mono PCM to `targetRate` and wrap it in a 16-bit PCM WAV. */
function samplesToWav(samples: Float32Array, sourceRate: number, targetRate: number): ArrayBuffer {
  const ratio = sourceRate / targetRate;
  const frames = Math.max(1, Math.floor(samples.length / ratio));
  const buffer = new ArrayBuffer(44 + frames * 2);
  const view = new DataView(buffer);

  const ascii = (offset: number, text: string): void => {
    for (let i = 0; i < text.length; i++) view.setUint8(offset + i, text.charCodeAt(i));
  };
  ascii(0, 'RIFF');
  view.setUint32(4, 36 + frames * 2, true);
  ascii(8, 'WAVE');
  ascii(12, 'fmt ');
  view.setUint32(16, 16, true); // PCM header size
  view.setUint16(20, 1, true); // format = PCM
  // Always MONO: the samples below are already downmixed, so the header must
  // say 1. Declaring a source channel count here would mislabel the file and
  // make decoders read it at the wrong speed.
  view.setUint16(22, 1, true);
  view.setUint32(24, targetRate, true);
  view.setUint32(28, targetRate * 2, true); // byte rate = rate * channels * 2
  view.setUint16(32, 2, true); // block align = channels * 2
  view.setUint16(34, 16, true); // bits per sample
  ascii(36, 'data');
  view.setUint32(40, frames * 2, true);

  const last = samples.length - 1;
  let offset = 44;
  for (let i = 0; i < frames; i++) {
    const position = i * ratio;
    const i0 = Math.min(Math.floor(position), last);
    const i1 = Math.min(i0 + 1, last);
    const frac = position - Math.floor(position);
    const value = samples[i0] * (1 - frac) + samples[i1] * frac;
    const clamped = Math.max(-1, Math.min(1, value));
    view.setInt16(offset, clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff, true);
    offset += 2;
  }
  return buffer;
}