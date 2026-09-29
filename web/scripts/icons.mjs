import { writeFile } from "node:fs/promises";
import { deflateSync } from "node:zlib";

// Draws the same mark as public/icon.svg into PNGs, without a native image library.
// The maskable variant shrinks the mark into the central safe zone on a full-bleed background.

function chunk(type, data) {
  const content = Buffer.concat([Buffer.from(type), data]);
  let crc = 0xffffffff;
  for (const byte of content) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit++) crc = (crc >>> 1) ^ (crc & 1 ? 0xedb88320 : 0);
  }
  const out = Buffer.alloc(data.length + 12);
  out.writeUInt32BE(data.length);
  content.copy(out, 4);
  out.writeUInt32BE((crc ^ 0xffffffff) >>> 0, out.length - 4);
  return out;
}

const BG = [91, 61, 245];
const INK = [255, 255, 255];
const DOT = [255, 212, 59];

/** Signed distance to a rounded rectangle: negative inside, positive outside. */
function roundedRectDistance(x, y, x0, y0, w, h, r) {
  const qx = Math.abs(x - (x0 + w / 2)) - (w / 2 - r);
  const qy = Math.abs(y - (y0 + h / 2)) - (h / 2 - r);
  return Math.hypot(Math.max(qx, 0), Math.max(qy, 0)) + Math.min(Math.max(qx, qy), 0) - r;
}

function segment(x, y, ax, ay, bx, by) {
  const t = Math.max(0, Math.min(1, ((x - ax) * (bx - ax) + (y - ay) * (by - ay)) / ((bx - ax) ** 2 + (by - ay) ** 2)));
  return Math.hypot(x - ax - t * (bx - ax), y - ay - t * (by - ay));
}

function sample(px, py, maskable) {
  // Coordinates in the 512 design space; the maskable icon scales the mark to 70 % around the centre.
  const s = maskable ? 0.7 : 1;
  const x = (px - 256) / s + 256;
  const y = (py - 256) / s + 256;
  if (!maskable) {
    const corner = roundedRectDistance(px, py, 0, 0, 512, 512, 112);
    if (corner > 0) return null; // transparent outside the rounded tile
  }
  if (Math.hypot(x - 318, y - 306) <= 30) return DOT;
  const frame = Math.abs(roundedRectDistance(x, y, 112, 136, 288, 256, 40)) <= 16;
  const bar = segment(x, y, 112, 216, 400, 216) <= 16 || segment(x, y, 192, 104, 192, 168) <= 16 || segment(x, y, 320, 104, 320, 168) <= 16;
  return frame || bar ? INK : BG;
}

async function draw(size, name, maskable = false) {
  const stride = size * 4 + 1;
  const pixels = Buffer.alloc(stride * size);
  const n = 3; // 3×3 supersampling for smooth edges
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      let r = 0, g = 0, b = 0, a = 0;
      for (let sy = 0; sy < n; sy++) {
        for (let sx = 0; sx < n; sx++) {
          const c = sample(((x + (sx + 0.5) / n) * 512) / size, ((y + (sy + 0.5) / n) * 512) / size, maskable);
          if (c) { r += c[0]; g += c[1]; b += c[2]; a += 1; }
        }
      }
      const i = y * stride + 1 + x * 4;
      const total = n * n;
      pixels[i] = a ? r / a : 0;
      pixels[i + 1] = a ? g / a : 0;
      pixels[i + 2] = a ? b / a : 0;
      pixels[i + 3] = (a / total) * 255;
    }
  }
  const header = Buffer.alloc(13);
  header.writeUInt32BE(size, 0);
  header.writeUInt32BE(size, 4);
  header[8] = 8;
  header[9] = 6; // RGBA
  await writeFile(new URL(`../dist/${name}`, import.meta.url), Buffer.concat([
    Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
    chunk("IHDR", header), chunk("IDAT", deflateSync(pixels)), chunk("IEND", Buffer.alloc(0)),
  ]));
}

await draw(180, "icon-180.png", true);
await draw(192, "icon-192.png");
await draw(512, "icon-512.png");
await draw(512, "icon-maskable-512.png", true);
