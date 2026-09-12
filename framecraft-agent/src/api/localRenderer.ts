import { unzipSync, strFromU8 } from 'fflate';
import { createContext, destroyContext, domToCanvas } from 'modern-screenshot';
import {
  AudioBufferSource,
  BufferTarget,
  CanvasSource,
  Mp4OutputFormat,
  Output,
  Quality,
  canEncodeAudio,
  canEncodeVideo,
} from 'mediabunny';
import gsapSource from 'gsap/dist/gsap.min.js?raw';
import hyperframesRuntimeSource from 'hyperframes/dist/hyperframe.runtime.iife.js?raw';

type BundleFiles = Record<string, Uint8Array>;

interface RenderManifest {
  expected_duration_s?: number;
  fps?: number;
  scene_samples?: Array<{ start_s?: number; end_s?: number }>;
}

interface HyperframesPlayerBridge {
  getDuration(): number;
  renderSeek(time: number): void;
  pause(): void;
}

interface RenderWindow extends Window {
  __player?: HyperframesPlayerBridge;
}

const TEXT_EXTENSIONS = /\.(?:html?|css|js|json|svg|txt|md)$/i;
const CAPTURE_STYLE_PROPERTIES = [
  'position', 'inset', 'top', 'right', 'bottom', 'left', 'z-index', 'display', 'overflow',
  'width', 'height', 'min-width', 'min-height', 'max-width', 'max-height', 'box-sizing',
  'margin', 'padding', 'transform', 'transform-origin', 'opacity', 'visibility',
  'background', 'background-color', 'background-image', 'background-size', 'background-position',
  'border', 'border-color', 'border-radius', 'box-shadow', 'filter', 'clip-path',
  'color', 'font', 'font-family', 'font-size', 'font-style', 'font-weight', 'line-height',
  'letter-spacing', 'text-align', 'text-shadow', 'white-space',
  'align-items', 'align-content', 'justify-content', 'justify-items', 'gap',
  'grid', 'grid-template-columns', 'grid-template-rows', 'grid-column', 'grid-row',
  'flex', 'flex-basis', 'flex-direction', 'flex-grow', 'flex-shrink', 'flex-wrap',
  'object-fit', 'object-position', 'mix-blend-mode', 'isolation',
  'fill', 'fill-opacity', 'stroke', 'stroke-width', 'stroke-dasharray', 'stroke-dashoffset',
];

function normalizePath(path: string) {
  const result: string[] = [];
  for (const part of path.replace(/\\/g, '/').split('/')) {
    if (!part || part === '.') continue;
    if (part === '..') result.pop();
    else result.push(part);
  }
  return result.join('/');
}

function findFile(files: BundleFiles, suffix: string) {
  const normalized = normalizePath(suffix);
  return Object.keys(files).find((name) => normalizePath(name).endsWith(normalized));
}

function mimeFor(path: string) {
  const ext = path.split('.').pop()?.toLowerCase();
  return ({
    png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg', webp: 'image/webp', gif: 'image/gif',
    svg: 'image/svg+xml', wav: 'audio/wav', mp3: 'audio/mpeg', m4a: 'audio/mp4', aac: 'audio/aac',
    mp4: 'video/mp4', webm: 'video/webm', woff: 'font/woff', woff2: 'font/woff2', ttf: 'font/ttf',
    otf: 'font/otf',
  } as Record<string, string>)[ext || ''] || 'application/octet-stream';
}

function escapeRegExp(value: string) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function rewriteReferences(source: string, sourcePath: string, urls: Map<string, string>) {
  let output = source;
  const base = sourcePath.includes('/') ? sourcePath.slice(0, sourcePath.lastIndexOf('/') + 1) : '';
  for (const [targetPath, url] of urls) {
    const relative = targetPath.startsWith(base) ? targetPath.slice(base.length) : targetPath;
    for (const candidate of new Set([targetPath, './' + targetPath, relative, './' + relative])) {
      if (!candidate || candidate === './') continue;
      output = output.replace(new RegExp(escapeRegExp(candidate), 'g'), url);
    }
  }
  return output;
}

function parseJson<T>(files: BundleFiles, suffix: string): T | null {
  const path = findFile(files, suffix);
  if (!path) return null;
  try {
    return JSON.parse(strFromU8(files[path])) as T;
  } catch {
    return null;
  }
}

function makeContactTimes(manifest: RenderManifest, duration: number) {
  const scenes = manifest.scene_samples || [];
  const selected = scenes.length > 6
    ? Array.from({ length: 6 }, (_, i) => scenes[Math.round(i * (scenes.length - 1) / 5)])
    : scenes;
  const times = selected.flatMap((scene) => {
    const start = Number(scene?.start_s || 0);
    const end = Math.min(duration, Number(scene?.end_s || duration));
    const span = Math.max(0, end - start);
    return [start + span * 0.18, start + span * 0.5, start + span * 0.82];
  });
  return (times.length ? times : [duration * 0.18, duration * 0.5, duration * 0.82])
    .filter((time) => Number.isFinite(time) && time >= 0 && time <= duration);
}

function nextPaint(win: Window) {
  return new Promise<void>((resolve) => win.requestAnimationFrame(() => win.requestAnimationFrame(() => resolve())));
}

async function waitForPlayer(iframe: HTMLIFrameElement, timeoutMs = 15_000) {
  const started = performance.now();
  while (performance.now() - started < timeoutMs) {
    const win = iframe.contentWindow as RenderWindow | null;
    if (win?.__player && win.__player.getDuration() > 0) return win.__player;
    await new Promise((resolve) => window.setTimeout(resolve, 100));
  }
  throw new Error('HyperFrames 工程未能在浏览器中启动，请检查工程 composition 和时间线。');
}

async function prepareComposition(bundle: Blob) {
  const files = unzipSync(new Uint8Array(await bundle.arrayBuffer()));
  const rootPath = findFile(files, 'index.html');
  if (!rootPath) throw new Error('工程包缺少 HyperFrames index.html。');
  const manifest = parseJson<RenderManifest>(files, 'local-render-manifest.json') || {};
  const urls = new Map<string, string>();
  const revoke: string[] = [];
  const registerUrl = (path: string, blob: Blob) => {
    const url = URL.createObjectURL(blob);
    urls.set(normalizePath(path), url);
    revoke.push(url);
  };

  for (const [rawPath, content] of Object.entries(files)) {
    const path = normalizePath(rawPath);
    if (!path || rawPath.endsWith('/') || path === normalizePath(rootPath) || TEXT_EXTENSIONS.test(path)) continue;
    registerUrl(path, new Blob([content], { type: mimeFor(path) }));
  }
  const htmlPaths = Object.keys(files)
    .map(normalizePath)
    .filter((path) => path !== normalizePath(rootPath) && /\.html?$/i.test(path));
  for (const path of htmlPaths) {
    const rawPath = Object.keys(files).find((candidate) => normalizePath(candidate) === path)!;
    registerUrl(path, new Blob(
      [rewriteReferences(strFromU8(files[rawPath]), path, urls)],
      { type: 'text/html;charset=utf-8' },
    ));
  }

  const runtimeUrl = URL.createObjectURL(new Blob([hyperframesRuntimeSource], { type: 'text/javascript' }));
  const gsapUrl = URL.createObjectURL(new Blob([gsapSource], { type: 'text/javascript' }));
  revoke.push(runtimeUrl, gsapUrl);
  let rootHtml = rewriteReferences(strFromU8(files[rootPath]), normalizePath(rootPath), urls);
  rootHtml = rootHtml.replace(/<script\b[^>]*src=["'][^"']*gsap[^"']*["'][^>]*>\s*<\/script>/gi, '');
  rootHtml = rootHtml.replace(/<\/head>/i, '<script src="' + gsapUrl + '"></script></head>');
  rootHtml = rootHtml.replace(/<\/body>/i, '<script src="' + runtimeUrl + '"></script></body>');

  const iframe = document.createElement('iframe');
  iframe.title = 'HyperFrames 浏览器渲染画布';
  iframe.setAttribute('aria-hidden', 'true');
  iframe.style.cssText = [
    'position:fixed', 'left:-20000px', 'top:0', 'width:1920px', 'height:1080px',
    'border:0', 'opacity:1', 'pointer-events:none', 'z-index:-1',
  ].join(';');
  document.body.appendChild(iframe);
  const loaded = new Promise<void>((resolve, reject) => {
    iframe.onload = () => resolve();
    iframe.onerror = () => reject(new Error('HyperFrames 浏览器画布加载失败。'));
  });
  iframe.srcdoc = rootHtml;
  await loaded;
  const player = await waitForPlayer(iframe);
  const doc = iframe.contentDocument;
  if (!doc?.body) throw new Error('HyperFrames 浏览器画布不可访问。');
  await doc.fonts?.ready;
  player.pause();
  const authoredRoot = doc.querySelector<HTMLElement>('[data-width][data-height]');
  const width = Math.max(2, Number(authoredRoot?.dataset.width || 1920));
  const height = Math.max(2, Number(authoredRoot?.dataset.height || 1080));
  iframe.style.width = width + 'px';
  iframe.style.height = height + 'px';
  return {
    iframe, player, doc, width, height,
    duration: Number(manifest.expected_duration_s || player.getDuration()),
    manifest, files,
    cleanup: () => {
      iframe.remove();
      revoke.forEach((url) => URL.revokeObjectURL(url));
    },
  };
}

async function decodeProjectAudio(files: BundleFiles) {
  const audioPath = Object.keys(files).find((path) => /\.(wav|mp3|m4a|aac)$/i.test(path));
  if (!audioPath) return null;
  const context = new AudioContext();
  try {
    const bytes = files[audioPath];
    const copy = new Uint8Array(bytes).buffer;
    return await context.decodeAudioData(copy);
  } finally {
    await context.close();
  }
}

function browserSupportError() {
  if (!window.isSecureContext && !['localhost', '127.0.0.1'].includes(window.location.hostname)) {
    return '浏览器本地 MP4 编码需要 HTTPS，请使用 FrameCraft 的 HTTPS 地址。';
  }
  if (!('VideoEncoder' in window) || !('AudioEncoder' in window)) {
    return '当前浏览器不支持本地 MP4 编码，请使用最新版 Chrome 或 Edge。';
  }
  return null;
}

export async function checkLocalRenderer() {
  const reason = browserSupportError();
  if (reason) throw new Error(reason);
  const [video, audio] = await Promise.all([
    canEncodeVideo('avc', { width: 1920, height: 1080, bitrate: 8_000_000 }),
    canEncodeAudio('aac', { sampleRate: 48_000, numberOfChannels: 2, bitrate: 192_000 }),
  ]);
  if (!video || !audio) throw new Error('当前设备无法使用 H.264/AAC 编码，请升级 Chrome 或 Edge 后重试。');
  return { ok: true, busy: false };
}

export async function renderLocally(
  bundle: Blob,
  requestedFps: number,
  onProgress: (progress: number, step: string) => void,
): Promise<{ video: Blob; contactSheet: Blob; mediaValidation: Record<string, unknown> }> {
  await checkLocalRenderer();
  onProgress(1, '正在浏览器内解包 HyperFrames 工程');
  const composition = await prepareComposition(bundle);
  const fps = Math.max(12, Math.min(60, Math.round(requestedFps || composition.manifest.fps || 24)));
  const captureFps = Math.min(12, fps);
  const duration = composition.duration;
  if (!Number.isFinite(duration) || duration <= 0) {
    composition.cleanup();
    throw new Error('HyperFrames 工程时长无效。');
  }
  const outputCanvas = document.createElement('canvas');
  outputCanvas.width = composition.width + (composition.width % 2);
  outputCanvas.height = composition.height + (composition.height % 2);
  const outputContext = outputCanvas.getContext('2d', { alpha: false });
  if (!outputContext) {
    composition.cleanup();
    throw new Error('浏览器无法创建视频画布。');
  }
  const target = new BufferTarget();
  const output = new Output({ format: new Mp4OutputFormat(), target });
  const videoSource = new CanvasSource(outputCanvas, {
    codec: 'avc',
    quality: new Quality('high'),
    keyFrameInterval: 2,
  });
  output.addVideoTrack(videoSource, { frameRate: fps });
  const audioBuffer = await decodeProjectAudio(composition.files);
  let audioSource: AudioBufferSource | null = null;
  if (audioBuffer) {
    audioSource = new AudioBufferSource({ codec: 'aac', quality: new Quality('high') });
    output.addAudioTrack(audioSource);
  }

  const contactTimes = makeContactTimes(composition.manifest, duration);
  const contactWidth = 320;
  const contactHeight = Math.round(contactWidth * composition.height / composition.width);
  const contactColumns = Math.min(3, Math.max(1, contactTimes.length));
  const contactCanvas = document.createElement('canvas');
  contactCanvas.width = contactColumns * contactWidth;
  contactCanvas.height = Math.ceil(contactTimes.length / contactColumns) * contactHeight;
  const contactContext = contactCanvas.getContext('2d');
  const capturedContacts = new Set<number>();
  const frameDuration = 1 / fps;
  const captureFrameDuration = 1 / captureFps;
  const totalCaptureFrames = Math.ceil(duration * captureFps);
  const screenshotContext = await createContext(composition.doc.body, {
    width: composition.width,
    height: composition.height,
    scale: 1,
    backgroundColor: '#000',
    font: false,
    timeout: 10_000,
    drawImageInterval: 0,
    includeStyleProperties: CAPTURE_STYLE_PROPERTIES,
  });
  try {
    await output.start();
    if (audioSource && audioBuffer) await audioSource.add(audioBuffer);
    for (let frame = 0; frame < totalCaptureFrames; frame += 1) {
      const time = Math.min(duration, frame * captureFrameDuration);
      composition.player.renderSeek(time);
      await nextPaint(composition.iframe.contentWindow!);
      const captured = await domToCanvas(screenshotContext);
      outputContext.drawImage(captured, 0, 0, outputCanvas.width, outputCanvas.height);
      const nextCaptureTime = Math.min(duration, (frame + 1) * captureFrameDuration);
      const firstOutputFrame = Math.round(time * fps);
      const lastOutputFrame = Math.max(firstOutputFrame + 1, Math.round(nextCaptureTime * fps));
      for (let outputFrame = firstOutputFrame; outputFrame < lastOutputFrame; outputFrame += 1) {
        const outputTime = outputFrame * frameDuration;
        if (outputTime >= duration) break;
        await videoSource.add(outputTime, Math.min(frameDuration, duration - outputTime), {
          keyFrame: outputFrame % Math.max(1, fps * 2) === 0,
        });
      }
      contactTimes.forEach((sampleTime, index) => {
        if (contactContext && !capturedContacts.has(index) && Math.abs(time - sampleTime) <= captureFrameDuration / 2) {
          const x = (index % contactColumns) * contactWidth;
          const y = Math.floor(index / contactColumns) * contactHeight;
          contactContext.drawImage(outputCanvas, x, y, contactWidth, contactHeight);
          capturedContacts.add(index);
        }
      });
      if (frame % Math.max(1, Math.round(captureFps / 2)) === 0 || frame === totalCaptureFrames - 1) {
        const progress = 5 + Math.round((frame + 1) / totalCaptureFrames * 90);
        onProgress(progress, 'HyperFrames 浏览器渲染 ' + Math.min(duration, time).toFixed(1) + ' / ' + duration.toFixed(1) + ' 秒');
      }
    }
    onProgress(97, '正在浏览器内封装 MP4');
    await output.finalize();
    if (!target.buffer) throw new Error('浏览器未返回 MP4 数据。');
    const contactSheet = await new Promise<Blob>((resolve, reject) => {
      contactCanvas.toBlob((blob) => blob ? resolve(blob) : reject(new Error('验收联系表生成失败。')), 'image/jpeg', 0.86);
    });
    onProgress(100, '浏览器本地渲染完成');
    return {
      video: new Blob([target.buffer], { type: 'video/mp4' }),
      contactSheet,
      mediaValidation: {
        renderer: 'hyperframes-browser', duration_s: duration,
        width: outputCanvas.width, height: outputCanvas.height, fps, capture_fps: captureFps,
        has_video: true, has_audio: Boolean(audioBuffer),
        contact_sample_times: contactTimes,
      },
    };
  } finally {
    destroyContext(screenshotContext);
    composition.cleanup();
  }
}
