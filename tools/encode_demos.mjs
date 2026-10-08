// Encode the recorded webm clips into cropped, palette-quantised GIFs for
// docs/media/. Run after tools/record_demos.mjs.
//
//   node tools/encode_demos.mjs
//
// Notes that cost time to learn:
//  * Playwright writes variable-frame-rate webm. `fps=` resamples to constant
//    frame rate using real timestamps, which preserves elapsed time. Renaming
//    frames instead (setpts=N/FRAME_RATE/TB) silently collapses the clip to
//    its frame-count/12 seconds and loses all pacing.
//  * -vf mpdecimate drops near-duplicate frames, which breaks GIF frame
//    timing the same way; avoid it here.

import { execFileSync } from 'node:child_process';
import { mkdirSync, readFileSync, statSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const REC = join(fileURLToPath(new URL('../.recording/', import.meta.url)));
const VIDEO_DIR = join(REC, 'videos');
const OUT_DIR = join(fileURLToPath(new URL('../docs/media/', import.meta.url)));

const OUT_WIDTH = 600;   // README column width
const FPS = 10;
const COLORS = 96;
const PAD = 6;           // css px of padding around the measured node box
const LEAD_MARGIN = 0.4; // seconds of slack when trimming the recorded lead-in

const meta = JSON.parse(readFileSync(join(VIDEO_DIR, 'meta.json'), 'utf8'));
const scale = meta.videoScale ?? 1;
mkdirSync(OUT_DIR, { recursive: true });

const even = (n) => (n % 2 === 0 ? n : n + 1);
const rows = [];

for (const demo of meta.demos) {
  const r = demo.rect;
  if (!r) { console.log(`SKIP ${demo.name}: no rect recorded`); continue; }

  const cropW = even(Math.round((r.width + PAD * 2) * scale));
  const cropH = even(Math.round((r.height + PAD * 2) * scale));
  const cropX = even(Math.round((r.x - PAD) * scale));
  const cropY = even(Math.round((r.y - PAD) * scale));

  const out = join(OUT_DIR, `${demo.name}.gif`);

  // Two-pass palette: generate from the clip, then apply, which beats letting
  // ffmpeg guess per-frame and avoids palette flicker.
  //
  // Note the separators: the first chain is joined with ',' because it is one
  // continuous graph starting from [0:v]; ';' would start a new chain and
  // ffmpeg reports "No such filter: ''".
  const vf = [
    `[0:v]crop=${cropW}:${cropH}:${cropX}:${cropY}`,
    `scale=${OUT_WIDTH}:-1:flags=lanczos`,
    `fps=${FPS}`,
    'split[a][b]',
  ].join(',')
    + `;[a]palettegen=max_colors=${COLORS}[p];[b][p]paletteuse=dither=bayer:bayer_scale=3`;

  execFileSync('ffmpeg', [
    '-y', '-v', 'error',
    // Trim the page-load + settle/clear head, which is dead air.
    '-ss', String(Math.max(0, (demo.leadIn ?? 0) + LEAD_MARGIN)),
    '-i', demo.video,
    '-filter_complex', vf, '-loop', '0', out,
  ], { stdio: 'inherit' });

  // Verify duration survived: a collapsed clip would be a fraction of expected.
  const probe = execFileSync('ffprobe', [
    '-v', 'error', '-select_streams', 'v:0',
    '-show_entries', 'stream=nb_frames,width,height',
    '-show_entries', 'format=duration',
    '-of', 'json', out,
  ]).toString();
  const info = JSON.parse(probe);
  const dur = Number(info.format?.duration ?? 0);
  const kb = Math.round(statSync(out).size / 1024);

  const src = execFileSync('ffprobe', [
    '-v', 'error', '-select_streams', 'v:0',
    '-show_entries', 'format=duration', '-of', 'default=nw=1:nk=1', demo.video,
  ]).toString().trim();
  const srcDur = Number(src);
  // The lead-in is deliberately trimmed, so the expected GIF length is the
  // source minus that, not the full source duration.
  const expected = Math.max(0.5, srcDur - (demo.leadIn ?? 0));

  rows.push({ name: demo.name, dur: dur.toFixed(1), srcDur: (srcDur || 0).toFixed(1), expected: expected.toFixed(1), size: `${info.streams?.[0]?.width}x${info.streams?.[0]?.height}`, kb, lines: demo.lines });
  console.log(`  ${demo.name}: ${dur.toFixed(1)}s (expected ~${expected.toFixed(1)}s of ${srcDur || '?'}s) ${info.streams?.[0]?.width}x${info.streams?.[0]?.height} ${kb}KB`);
}

writeFileSync(join(VIDEO_DIR, 'encode-report.json'), JSON.stringify(rows, null, 2));

const total = rows.reduce((a, b) => a + b.kb, 0);
console.log(`\n${rows.length} GIFs, ${total} KB total`);
const bad = rows.filter((r) => Math.abs(Number(r.dur) - Number(r.expected)) > Number(r.expected) * 0.25);
if (bad.length) console.log('WARNING: duration off target for:', bad.map((b) => `${b.name} (${b.dur}s vs ~${b.expected}s)`).join(', '));
else console.log('all durations match their trimmed target');