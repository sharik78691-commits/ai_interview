// Generates a professional tray icon: dark navy rounded square with a
// periwinkle sound-wave mark (matches the app theme: bg #0b1020,
// accent #6e8bff). Rendered with 4x supersampling, packed as multi-size
// PNG-in-ICO (16/32/48/256) — valid on Windows Vista+.
const zlib = require('zlib');
const fs = require('fs');
const path = require('path');

const BG = [11, 16, 32]; // #0b1020
const BAR_TOP = [168, 188, 255]; // #a8bcff
const BAR_BOT = [91, 116, 240]; // #5b74f0

function makeCRC() {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    t[n] = c;
  }
  return (buf) => {
    let c = 0xffffffff;
    for (let i = 0; i < buf.length; i++) c = t[(c ^ buf[i]) & 0xff] ^ (c >>> 8);
    return (c ^ 0xffffffff) >>> 0;
  };
}
const crc32 = makeCRC();

function pngEncode(w, h, rgba) {
  const raw = Buffer.alloc((w * 4 + 1) * h);
  for (let y = 0; y < h; y++) {
    raw[y * (w * 4 + 1)] = 0;
    Buffer.from(rgba.buffer, rgba.byteOffset + y * w * 4, w * 4).copy(raw, y * (w * 4 + 1) + 1);
  }
  const ihdr = Buffer.alloc(25);
  ihdr.writeUInt32BE(13, 0);
  ihdr.write('IHDR', 4);
  ihdr.writeUInt32BE(w, 8);
  ihdr.writeUInt32BE(h, 12);
  ihdr[16] = 8; ihdr[17] = 6; // 8-bit RGBA
  ihdr.writeUInt32BE(crc32(ihdr.subarray(4, 21)), 21);
  const comp = zlib.deflateSync(raw, { level: 9 });
  const idat = Buffer.alloc(12 + comp.length);
  idat.writeUInt32BE(comp.length, 0);
  idat.write('IDAT', 4);
  comp.copy(idat, 8);
  idat.writeUInt32BE(crc32(idat.subarray(4, 8 + comp.length)), 8 + comp.length);
  const iend = Buffer.from([0, 0, 0, 0, 73, 69, 78, 68, 174, 66, 96, 130]);
  const sig = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);
  return Buffer.concat([sig, ihdr, idat, iend]);
}

// Draw at 4x with hard edges, then box-downsample for smooth antialiasing.
function render(size) {
  const SS = 4, W = size * SS, H = size * SS;
  const buf = new Uint8ClampedArray(W * H * 4);
  const R = 0.24 * W; // corner radius
  const inRound = (x, y) => {
    const cx = Math.min(Math.max(x, R), W - R);
    const cy = Math.min(Math.max(y, R), H - R);
    const dx = x - cx, dy = y - cy;
    return dx * dx + dy * dy <= R * R;
  };
  // Sound bars: symmetric heights across the middle band.
  const heights = [0.42, 0.68, 1.0, 0.68, 0.42];
  const bandX0 = 0.24 * W, bandX1 = 0.76 * W, midY = 0.5 * H;
  const slotW = (bandX1 - bandX0) / heights.length;
  const barW = slotW * 0.52, maxHalf = 0.27 * H;
  const inBar = (x, y) => {
    for (let i = 0; i < heights.length; i++) {
      const cx = bandX0 + slotW * (i + 0.5);
      const hw = barW / 2, hh = (maxHalf * heights[i]);
      const qx = Math.min(Math.max(x, cx - hw), cx + hw);
      const qy = Math.min(Math.max(y, midY - hh), midY + hh);
      const dx = x - qx, dy = y - qy;
      if (dx * dx + dy * dy <= (hw * hw) + 1) return { cx, y, hh };
    }
    return null;
  };
  for (let y = 0; y < H; y++) {
    for (let x = 0; x < W; x++) {
      const i = (y * W + x) * 4;
      if (!inRound(x + 0.5, y + 0.5)) continue; // transparent outside
      const hit = inBar(x + 0.5, y + 0.5);
      if (hit) {
        const t = Math.min(1, Math.max(0, (y + 0.5 - (midY - hit.hh)) / (2 * hit.hh || 1)));
        buf[i] = BAR_TOP[0] + (BAR_BOT[0] - BAR_TOP[0]) * t;
        buf[i + 1] = BAR_TOP[1] + (BAR_BOT[1] - BAR_TOP[1]) * t;
        buf[i + 2] = BAR_TOP[2] + (BAR_BOT[2] - BAR_TOP[2]) * t;
        buf[i + 3] = 255;
      } else {
        buf[i] = BG[0]; buf[i + 1] = BG[1]; buf[i + 2] = BG[2]; buf[i + 3] = 255;
      }
    }
  }
  // Downsample 4x4.
  const out = new Uint8ClampedArray(size * size * 4);
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      let r = 0, g = 0, b = 0, a = 0;
      for (let dy = 0; dy < SS; dy++) {
        for (let dx = 0; dx < SS; dx++) {
          const si = (((y * SS + dy) * W) + (x * SS + dx)) * 4;
          r += buf[si]; g += buf[si + 1]; b += buf[si + 2]; a += buf[si + 3];
        }
      }
      const di = (y * size + x) * 4, n = SS * SS;
      // Straight (non-premultiplied) output: un-weight color by alpha.
      const alpha = a / n / 255;
      out[di] = alpha > 0 ? r / n / alpha : 0;
      out[di + 1] = alpha > 0 ? g / n / alpha : 0;
      out[di + 2] = alpha > 0 ? b / n / alpha : 0;
      out[di + 3] = a / n;
    }
  }
  return { size, data: out };
}

function icoPack(images) {
  const blobs = images.map(({ size, data }) => pngEncode(size, size, data));
  const header = Buffer.alloc(6 + 16 * blobs.length);
  header.writeUInt16LE(0, 0);
  header.writeUInt16LE(1, 2);
  header.writeUInt16LE(blobs.length, 4);
  let offset = 6 + 16 * blobs.length;
  blobs.forEach((b, i) => {
    const s = images[i].size;
    header.writeUInt8(s >= 256 ? 0 : s, 6 + i * 16); // width
    header.writeUInt8(s >= 256 ? 0 : s, 7 + i * 16); // height
    header.writeUInt8(0, 8 + i * 16); // color count
    header.writeUInt8(0, 9 + i * 16); // reserved
    header.writeUInt16LE(1, 10 + i * 16); // planes
    header.writeUInt16LE(32, 12 + i * 16); // bit count
    header.writeUInt32LE(b.length, 14 + i * 16); // bytes in resource
    header.writeUInt32LE(offset, 18 + i * 16); // image offset
    offset += b.length;
  });
  return Buffer.concat([header, ...blobs]);
}

const images = [16, 32, 48, 256].map(render);
const outPath = path.join(__dirname, 'tray-icon.ico');
fs.writeFileSync(outPath, icoPack(images));
console.log('wrote', outPath, fs.statSync(outPath).size, 'bytes');
