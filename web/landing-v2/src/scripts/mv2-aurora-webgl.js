/**
 * melpis landing v2 — hero aurora mesh-gradient backdrop.
 *
 * Native WebGL only: no library, no network, no shader or texture assets, no inline style
 * attributes. One fullscreen fragment program paints a slow monochrome light-field mesh inside
 * the hero panel. The field is built from drifting soft light anchors, warped by low-frequency
 * value noise so no circular blob boundary is ever visible; the centre of the panel is damped so
 * the headline keeps its contrast.
 *
 * Guarantees, per ADR-005 (which extends the ADR-003 WebGL exception to a third, scoped surface):
 *  - returns null (never throws) when a context cannot be created or the program fails,
 *  - monochrome output only: the palette stays within the graphite / silver / paper ladder,
 *  - drives its own time and entrance ramp internally, so no animation library touches a uniform,
 *  - pauses when the panel leaves the viewport or the document is hidden,
 *  - adaptive resolution so a slow device renders fewer pixels instead of dropping frames,
 *  - releases program, buffer, listeners and context on stop() and on context loss.
 */

const VERTEX = `attribute vec2 a_pos;
void main(){ gl_Position = vec4(a_pos, 0.0, 1.0); }`;

const FRAGMENT = `precision mediump float;
uniform vec2 u_res;
uniform float u_time;
uniform float u_in;
uniform vec2 u_ptr;

float hash(vec2 p){ return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453123); }

float noise(vec2 p){
  vec2 i = floor(p);
  vec2 f = fract(p);
  vec2 u = f * f * (3.0 - 2.0 * f);
  return mix(
    mix(hash(i), hash(i + vec2(1.0, 0.0)), u.x),
    mix(hash(i + vec2(0.0, 1.0)), hash(i + vec2(1.0, 1.0)), u.x),
    u.y
  );
}

float fbm(vec2 p){
  float v = 0.0;
  float a = 0.5;
  for (int k = 0; k < 2; k++){ v += a * noise(p); p = p * 2.04 + vec2(13.7, 6.1); a *= 0.5; }
  return v;
}

float glow(vec2 p, vec2 c, float r){
  float d = length((p - c) / r);
  return exp(-d * d * 1.7);
}

void main(){
  vec2 uv = (gl_FragCoord.xy - 0.5 * u_res) / max(u_res.y, 1.0);
  float t = u_time;
  vec2 ptr = (u_ptr - 0.5) * 2.0;

  vec2 warp = vec2(
    fbm(uv * 1.06 + vec2(0.0, t * 0.048)),
    fbm(uv * 1.06 + vec2(5.3, -t * 0.042))
  ) - 0.5;
  vec2 q = uv + warp * 0.48 + ptr * 0.05;

  float f = 0.0;
  f += (0.86 + 0.22 * sin(t * 0.560)) * glow(q, vec2(-0.74 + 0.12 * sin(t * 0.520), 0.44 + 0.08 * cos(t * 0.720)) + ptr * 0.06, 0.66);
  f += (0.94 + 0.20 * cos(t * 0.620)) * glow(q, vec2(0.88 + 0.11 * cos(t * 0.720), 0.20 + 0.09 * sin(t * 0.560)) - ptr * 0.05, 0.60);
  f += (0.62 + 0.20 * sin(t * 0.720 + 1.7)) * glow(q, vec2(0.06 + 0.18 * sin(t * 0.660 + 0.9), -0.70 + 0.09 * cos(t * 0.560)), 0.74);
  f += (0.44 + 0.16 * cos(t * 0.660 + 2.4)) * glow(q, vec2(-0.66 + 0.10 * cos(t * 0.560 + 2.1), -0.58 + 0.08 * sin(t * 0.720)), 0.52);
  f += (0.34 + 0.15 * sin(t * 0.560 + 3.4)) * glow(q, vec2(0.54 + 0.11 * sin(t * 0.660 + 3.4), 0.56 + 0.07 * cos(t * 0.720 + 0.6)), 0.46);
  f += (0.30 + 0.13 * cos(t * 0.620 + 4.1)) * glow(q, vec2(-0.16 + 0.14 * sin(t * 0.720 + 1.2), 0.06 + 0.10 * cos(t * 0.560 + 2.8)), 0.42);

  f *= 0.66 + 0.86 * fbm(uv * 0.72 + vec2(t * 0.030, -t * 0.024));
  f *= 0.52 + 0.48 * smoothstep(0.10, 0.92, length(uv * vec2(0.82, 1.28)));
  f = f / (1.0 + f);
  f = smoothstep(0.048, 0.72, f) * (0.62 + 0.38 * fbm(uv * 1.8 + vec2(-t * 0.060, t * 0.050)));

  vec3 c0 = vec3(0.0196, 0.0235, 0.0275);
  vec3 c1 = vec3(0.0471, 0.0549, 0.0667);
  vec3 c2 = vec3(0.1255, 0.1373, 0.1569);
  vec3 c3 = vec3(0.2863, 0.3020, 0.3255);
  vec3 c4 = vec3(0.5608, 0.5765, 0.6000);
  vec3 c5 = vec3(0.8706, 0.8824, 0.8980);
  vec3 c6 = vec3(0.9765, 0.9843, 0.9922);

  vec3 col = mix(c0, c1, smoothstep(0.0, 0.46, uv.y + 0.5));
  col = mix(col, c2, smoothstep(0.02, 0.28, f));
  col = mix(col, c3, smoothstep(0.20, 0.44, f));
  col = mix(col, c4, smoothstep(0.36, 0.58, f));
  col = mix(col, c5, smoothstep(0.52, 0.76, f));
  col = mix(col, c6, smoothstep(0.68, 0.90, f));

  col *= mix(0.86, 1.08, u_in);
  col += (hash(gl_FragCoord.xy) - 0.5) / 255.0;

  gl_FragColor = vec4(max(col, 0.0), 1.0);
}`;

function compile(gl, type, source) {
  const shader = gl.createShader(type);
  if (!shader) return null;
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    gl.deleteShader(shader);
    return null;
  }
  return shader;
}

export function mountAurora(canvas, options) {
  if (!canvas || typeof canvas.getContext !== 'function') return null;
  const settings = options || {};

  let gl = null;
  const contexts = ['webgl', 'experimental-webgl'];
  for (let index = 0; index < contexts.length && !gl; index += 1) {
    try {
      gl = canvas.getContext(contexts[index], {
        alpha: false,
        antialias: false,
        depth: false,
        stencil: false,
        preserveDrawingBuffer: false,
        powerPreference: 'low-power',
      });
    } catch (error) {
      gl = null;
    }
  }
  if (!gl) return null;

  const vertex = compile(gl, gl.VERTEX_SHADER, VERTEX);
  const fragment = compile(gl, gl.FRAGMENT_SHADER, FRAGMENT);
  const program = vertex && fragment ? gl.createProgram() : null;
  if (!program) {
    if (vertex) gl.deleteShader(vertex);
    if (fragment) gl.deleteShader(fragment);
    return null;
  }
  gl.attachShader(program, vertex);
  gl.attachShader(program, fragment);
  gl.linkProgram(program);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
    gl.deleteProgram(program);
    gl.deleteShader(vertex);
    gl.deleteShader(fragment);
    return null;
  }
  gl.deleteShader(vertex);
  gl.deleteShader(fragment);

  const buffer = gl.createBuffer();
  const loc = {
    pos: gl.getAttribLocation(program, 'a_pos'),
    res: gl.getUniformLocation(program, 'u_res'),
    time: gl.getUniformLocation(program, 'u_time'),
    intro: gl.getUniformLocation(program, 'u_in'),
    pointer: gl.getUniformLocation(program, 'u_ptr'),
  };
  gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
  gl.enableVertexAttribArray(loc.pos);
  gl.vertexAttribPointer(loc.pos, 2, gl.FLOAT, false, 0, 0);
  gl.useProgram(program);

  let raf = 0;
  let started = 0;
  let stopped = false;
  let visible = true;
  let scale = 0.5;
  let dpr = 1;
  let ema = 16;
  let last = 0;
  let pointerX = 0.5;
  let pointerY = 0.5;
  let pointerTargetX = 0.5;
  let pointerTargetY = 0.5;
  let onLost = null;

  const finePointer = (() => {
    try {
      return window.matchMedia('(hover: hover) and (pointer: fine)').matches;
    } catch (error) {
      return false;
    }
  })();

  const onPointer = (event) => {
    const rect = canvas.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    pointerTargetX = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
    pointerTargetY = Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height));
  };

  const onVisibility = () => {
    if (document.hidden) stopLoop();
    else startLoop();
  };

  const lost = (event) => {
    event.preventDefault();
    stopLoop();
    if (typeof onLost === 'function') onLost();
  };

  const observer = typeof window !== 'undefined' && typeof window.IntersectionObserver === 'function'
    ? new window.IntersectionObserver((entries) => {
      visible = entries.some((entry) => entry.isIntersecting);
      if (visible) startLoop();
      else stopLoop();
    }, { rootMargin: '120px 0px' })
    : null;

  function resize() {
    const rect = canvas.getBoundingClientRect();
    const width = Math.max(1, Math.round((rect.width || 1) * dpr * scale));
    const height = Math.max(1, Math.round((rect.height || 1) * dpr * scale));
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
    }
    gl.viewport(0, 0, width, height);
  }

  function frame(time) {
    if (stopped || !visible) return;
    if (!started) started = time;
    const elapsed = (time - started) / 1000;
    const delta = last ? time - last : 16;
    last = time;
    ema = ema * 0.9 + delta * 0.1;
    if (ema > 23 && scale > 0.34) {
      scale = Math.max(0.34, scale - 0.06);
      resize();
    }
    const enter = Math.min(1, elapsed / 1.1);
    pointerX += (pointerTargetX - pointerX) * 0.05;
    pointerY += (pointerTargetY - pointerY) * 0.05;
    resize();
    gl.uniform2f(loc.res, canvas.width, canvas.height);
    gl.uniform1f(loc.time, elapsed);
    gl.uniform1f(loc.intro, enter * enter * (3.0 - 2.0 * enter));
    gl.uniform2f(loc.pointer, pointerX, 1 - pointerY);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
    raf = window.requestAnimationFrame(frame);
  }

  function startLoop() {
    if (stopped || raf || !visible || document.hidden) return;
    raf = window.requestAnimationFrame(frame);
  }

  function stopLoop() {
    if (raf) window.cancelAnimationFrame(raf);
    raf = 0;
  }

  function stop() {
    if (stopped) return;
    stopped = true;
    stopLoop();
    if (observer) observer.disconnect();
    if (finePointer) window.removeEventListener('pointermove', onPointer, false);
    document.removeEventListener('visibilitychange', onVisibility, false);
    canvas.removeEventListener('webglcontextlost', lost, false);
    try {
      gl.deleteProgram(program);
      gl.deleteBuffer(buffer);
      const lose = gl.getExtension('WEBGL_lose_context');
      if (lose) lose.loseContext();
    } catch (error) {
      /* context already gone */
    }
  }

  canvas.addEventListener('webglcontextlost', lost, false);
  if (finePointer) window.addEventListener('pointermove', onPointer, { passive: true });
  document.addEventListener('visibilitychange', onVisibility, false);
  if (observer) observer.observe(settings.observe || canvas);

  return {
    start() {
      if (stopped || raf) return;
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      resize();
      startLoop();
    },
    onLost(handler) {
      onLost = handler;
    },
    stop,
  };
}
