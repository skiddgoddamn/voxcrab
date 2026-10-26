'use strict';
/* Renderer page for the voice-widget web engines. Python (web_child.py) sends JSON commands through window.vw.cmd(...),
   the page answers with emit({ev: ...}) -> window.pywebview.api.emit. Two adapters: Live2D (pixi-live2d-display) and VRM (three-vrm). */
(() => {
  const canvas = document.getElementById('c');
  const S = { adapter: null, profiles: {}, emotion: 'idle', fps: 30, anim: true, paused: false, activeUntil: 0, last: 0, t: 0,
              cur: { dx: 0, dy: 0, rot: 0, sc: 1 }, mouth: 0, ext: null, extAt: 0, maskAt: 0, blink: { next: 3, p: -1 }, prof: null };
  const queue = [];
  const emit = (o) => { queue.push(JSON.stringify(o)); flush(); };
  function flush() {
    if (!(window.pywebview && window.pywebview.api && window.pywebview.api.emit)) return;
    while (queue.length) window.pywebview.api.emit(queue.shift());
  }
  setInterval(flush, 150);
  const fail = (e) => {
    const msg = String(e && e.message || e);
    const el = document.getElementById('err'); el.textContent = msg; el.style.display = 'block';
    emit({ ev: 'error', msg });
  };
  window.addEventListener('error', (e) => fail(e.error || e.message));
  window.addEventListener('unhandledrejection', (e) => fail(e.reason));

  canvas.addEventListener('pointerdown', (e) => { if (e.button === 0) emit({ ev: 'drag' }); });
  canvas.addEventListener('contextmenu', (e) => { e.preventDefault(); emit({ ev: 'menu' }); });

  const loadScript = (src) => new Promise((res, rej) => {
    const s = document.createElement('script'); s.src = src; s.onload = res; s.onerror = () => rej(new Error('Не загрузился скрипт ' + src));
    document.head.appendChild(s);
  });
  const encPath = (p) => p.split('/').map(encodeURIComponent).join('/');

  // ---------------------------------------------------------------- shared animation state
  const lerp = (a, b, k) => a + (b - a) * k;
  function blinkOpen(dt, prof) {          // 1 = eyes open, 0 = closed
    if (!prof.blink) return 0;            // sleeping: eyes shut
    if (!S.anim) return 1;
    const b = S.blink;
    if (b.p < 0) { b.next -= dt; if (b.next <= 0) { b.p = 0; } return 1; }
    b.p += dt / 0.18;
    if (b.p >= 1) { b.p = -1; b.next = 2.5 + Math.random() * 3.5; return 1; }
    return 1 - Math.sin(Math.PI * b.p);
  }
  function mouthTarget(prof) {
    if (prof.mouth !== 'talk') return 0;
    if (S.ext !== null && performance.now() - S.extAt < 400) return S.ext;   // real TTS amplitude from Python
    if (!S.anim) return 0;
    return Math.max(0, Math.sin(S.t * 13) * 0.6 + Math.sin(S.t * 5.1) * 0.4);
  }
  function bodyTarget(prof) {             // procedural motion: bob / sway / tilt / pulse (works for any model)
    const b = prof.body, t = S.t, w = b.speed;
    if (!S.anim) return { dx: 0, dy: 0, rot: 0.12 * b.tilt, sc: 1 };
    return { dx: 0.02 * b.sway * Math.sin(t * w * 0.7), dy: -0.015 * b.bob * Math.abs(Math.sin(t * w)),
             rot: 0.12 * b.tilt + 0.03 * b.sway * Math.sin(t * w * 0.5), sc: 1 + 0.04 * b.pulse * Math.sin(t * w * 2) };
  }

  // ---------------------------------------------------------------- Live2D adapter
  async function initLive2D(init) {
    await loadScript('core.js');
    if (!window.Live2DCubismCore) throw new Error('Файл не является Cubism Core (нет Live2DCubismCore). Укажите live2dcubismcore.min.js из Cubism SDK for Web.');
    await loadScript('v/pixi.min.js'); await loadScript('v/cubism4.min.js');
    const PIXI = window.PIXI, L = PIXI.live2d;
    if (!L || !L.Live2DModel) throw new Error('pixi-live2d-display не инициализировался');
    L.Live2DModel.registerTicker(PIXI.Ticker);
    const app = new PIXI.Application({ view: canvas, backgroundAlpha: 0, antialias: true, autoStart: false, autoDensity: true,
                                       resolution: Math.min(2, window.devicePixelRatio || 1), width: innerWidth, height: innerHeight });
    app.ticker.stop();
    const model = await L.Live2DModel.from('m/' + encPath(init.entry), { autoInteract: false, autoUpdate: false });
    app.stage.addChild(model);
    model.anchor.set(0.5, 1);
    model.scale.set(1);
    const w0 = model.width || 1, h0 = model.height || 1;
    let base = 1, oy = 0;
    const fit = () => { base = Math.min(innerWidth * 0.95 / w0, innerHeight * 0.95 / h0); };
    fit();
    const im = model.internalModel, core = im.coreModel;
    const hasParam = (id) => { try { return core.getParameterIndex(id) >= 0; } catch (e) { return false; } };
    const mouthIds = ((im.motionManager && im.motionManager.lipSyncIds) || []).filter(hasParam);
    if (!mouthIds.length && hasParam('ParamMouthOpenY')) mouthIds.push('ParamMouthOpenY');
    const eyeIds = ['ParamEyeLOpen', 'ParamEyeROpen'].filter(hasParam);
    const manualBlink = !im.eyeBlink;
    const st = { open: 1, mouth: 0, force: false };
    im.on('beforeModelUpdate', () => {
      if (st.mouth > 0.001 || st.wasMouth) for (const id of mouthIds) core.setParameterValueById(id, st.mouth);
      st.wasMouth = st.mouth > 0.001;
      if (st.force || manualBlink) for (const id of eyeIds) core.setParameterValueById(id, st.open);
    });
    const exprMgr = im.motionManager.expressionManager;
    return {
      info: { mouth: mouthIds.length > 0, blink: eyeIds.length > 0 || !manualBlink },
      setEmotion(prof) {
        try {
          if (prof.expression && exprMgr) model.expression(prof.expression).catch(() => {});
          else if (exprMgr) exprMgr.resetExpression();
          if (prof.motion) model.motion(prof.motion[0], prof.motion[1], 2).catch(() => {});
        } catch (e) { console.warn(e); }
      },
      frame(dt, prof, open, mouth, body) {
        st.open = open; st.mouth = mouth; st.force = !prof.blink;
        model.x = innerWidth / 2 + body.dx * innerWidth; model.y = innerHeight * 0.98 + oy + body.dy * innerHeight;
        model.rotation = body.rot; model.scale.set(base * body.sc);
        model.update(dt * 1000);
        app.renderer.render(app.stage);
      },
      resize() { app.renderer.resize(innerWidth, innerHeight); fit(); this.autofit(); },
      /* the model's canvas often has big empty margins (chibi at the bottom of a tall frame): scale and shift so the VISIBLE part fills ~90% of the height */
      autofit() {
        const flat = { dx: 0, dy: 0, rot: 0, sc: 1 };
        for (let i = 0; i < 3; i++) {
          this.frame(0.016, profile('idle'), 1, 0, flat); const b = measure(); if (!b) return;
          const vh = b[3] - b[1], vw = b[2] - b[0];
          const k = Math.max(0.4, Math.min(3, Math.min(0.9 / Math.max(vh, 0.05), 0.95 / Math.max(vw, 0.05))));
          if (Math.abs(k - 1) < 0.03 && Math.abs(b[3] - 0.98) < 0.02) break;
          base *= k; oy += (0.98 - b[3]) * innerHeight;
        }
      },
    };
  }

  // ---------------------------------------------------------------- VRM adapter
  const VRM_ALIAS = { joy: 'happy', sorrow: 'sad', fun: 'relaxed', a: 'aa', i: 'ih', u: 'ou', e: 'ee', o: 'oh' };
  async function initVRM(init) {
    await loadScript('v/vrm.bundle.js');
    const { THREE, GLTFLoader, VRMLoaderPlugin, VRMUtils } = window.VWVRM;
    const renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true, premultipliedAlpha: true });
    renderer.setClearColor(0x000000, 0);
    renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
    renderer.setSize(innerWidth, innerHeight, false);
    const scene = new THREE.Scene();
    const light = new THREE.DirectionalLight(0xffffff, Math.PI * 0.9); light.position.set(1, 1.5, 2); scene.add(light);
    scene.add(new THREE.AmbientLight(0xffffff, 0.9));
    const loader = new GLTFLoader(); loader.register((p) => new VRMLoaderPlugin(p));
    const gltf = await loader.loadAsync('m/' + encPath(init.entry));
    const vrm = gltf.userData.vrm;
    if (!vrm) throw new Error('Файл загружен, но VRM-данных в нём нет.');
    VRMUtils.rotateVRM0(vrm);
    scene.add(vrm.scene);
    const box = new THREE.Box3().setFromObject(vrm.scene), size = box.getSize(new THREE.Vector3()), ctr = box.getCenter(new THREE.Vector3());
    const camera = new THREE.PerspectiveCamera(25, innerWidth / innerHeight, 0.05, 100);
    const fitCam = () => {
      camera.aspect = innerWidth / innerHeight; camera.updateProjectionMatrix();
      const vf = (camera.fov * Math.PI) / 180;
      const dist = Math.max(size.y * 1.08 / (2 * Math.tan(vf / 2)), size.x * 1.3 / (2 * Math.tan(vf / 2) * camera.aspect));
      camera.position.set(ctr.x, ctr.y, ctr.z + dist); camera.lookAt(ctr.x, ctr.y, ctr.z);
    };
    fitCam();
    const em = vrm.expressionManager, hum = vrm.humanoid;
    const known = (n) => !!(em && em.getExpression(n));
    const resolve = (n) => (known(n) ? n : (VRM_ALIAS[n] && known(VRM_ALIAS[n]) ? VRM_ALIAS[n] : null));
    const bone = (n) => hum.getNormalizedBoneNode(n);
    const bones = { head: bone('head'), spine: bone('spine'), chest: bone('chest') || bone('upperChest'), lua: bone('leftUpperArm'), rua: bone('rightUpperArm'),
                    lla: bone('leftLowerArm'), rla: bone('rightLowerArm'), hips: bone('hips') };
    const st = { expr: null, w: 0 };
    const hipsY = bones.hips ? bones.hips.position.y : 0;
    const blinkName = resolve('blink'), mouthName = resolve('aa');
    return {
      info: { mouth: !!mouthName, blink: !!blinkName },
      setEmotion(prof) { st.expr = prof.expression ? resolve(prof.expression) : null; st.w = prof.expression_weight || 0; },
      frame(dt, prof, open, mouth, body) {
        const t = S.t;
        if (em) {
          em.resetValues();
          if (st.expr) em.setValue(st.expr, st.w);
          if (blinkName) em.setValue(blinkName, 1 - open);
          if (mouthName) em.setValue(mouthName, mouth);
        }
        // arms down from the T-pose (normalized rig), breathing, emotion-driven head / spine motion
        if (bones.lua) bones.lua.rotation.z = -1.25; if (bones.rua) bones.rua.rotation.z = 1.25;
        if (bones.lla) bones.lla.rotation.z = -0.15; if (bones.rla) bones.rla.rotation.z = 0.15;
        const breath = S.anim ? Math.sin(t * 1.6) * 0.012 : 0;
        if (bones.chest) bones.chest.rotation.x = breath;
        if (bones.spine) bones.spine.rotation.z = body.rot * 0.5;
        if (bones.head) { bones.head.rotation.z = body.rot; bones.head.rotation.x = mouth * 0.06 + (prof.body.bob > 0.3 ? 0.04 * Math.sin(t * prof.body.speed) : 0); }
        if (bones.hips) bones.hips.position.y = hipsY + body.dy * -size.y * 0.5;
        vrm.scene.position.x = body.dx * size.x; vrm.scene.scale.setScalar(body.sc);
        vrm.update(dt);
        renderer.render(scene, camera);
      },
      resize() { renderer.setSize(innerWidth, innerHeight, false); fitCam(); },
    };
  }

  // ---------------------------------------------------------------- opaque-pixel mask (for click-through everywhere except on the model)
  const MW = 60, MH = 84, mcan = document.createElement('canvas'); mcan.width = MW; mcan.height = MH;
  const mctx = mcan.getContext('2d', { willReadFrequently: true });
  const BW = 120, BH = 168, bcan = document.createElement('canvas'); bcan.width = BW; bcan.height = BH;
  const bctx = bcan.getContext('2d', { willReadFrequently: true });
  /* visible (non-transparent) bounds of the current render as [left, top, right, bottom] fractions of the window, or null */
  function measure() {
    try {
      bctx.clearRect(0, 0, BW, BH); bctx.drawImage(canvas, 0, 0, BW, BH);
      const d = bctx.getImageData(0, 0, BW, BH).data; let l = BW, t = BH, r = -1, b = -1;
      for (let y = 0; y < BH; y++) for (let x = 0; x < BW; x++) if (d[(y * BW + x) * 4 + 3] > 40) { if (x < l) l = x; if (x > r) r = x; if (y < t) t = y; if (y > b) b = y; }
      return r < 0 ? null : [l / BW, t / BH, (r + 1) / BW, (b + 1) / BH];
    } catch (e) { return null; }
  }
  function sendMask() {
    try {
      mctx.clearRect(0, 0, MW, MH); mctx.drawImage(canvas, 0, 0, MW, MH);   // same task as the render: the WebGL buffer is still valid
      const d = mctx.getImageData(0, 0, MW, MH).data; let bits = '';
      for (let i = 3; i < d.length; i += 4) bits += d[i] > 12 ? '1' : '0';
      emit({ ev: 'mask', w: MW, h: MH, bits, box: S.box = measure() || S.box });
    } catch (e) { /* mask is optional: without it the window simply stays clickable */ }
  }

  // ---------------------------------------------------------------- main loop and commands
  function profile(name) { return S.profiles[name] || S.profiles.idle || { expression: null, expression_weight: 0, motion: null, mouth: 'none', blink: true, body: { bob: 0, sway: 0, tilt: 0, pulse: 0, speed: 1 } }; }
  function setEmotion(name) {
    S.emotion = name; S.prof = profile(name); S.activeUntil = performance.now() + 2500;
    if (S.adapter) S.adapter.setEmotion(S.prof);
  }
  function tick(now) {
    requestAnimationFrame(tick);
    if (!S.adapter || S.paused) return;
    if (now - S.last < 1000 / S.fps - 2) return;
    const dt = Math.min(0.1, (now - S.last) / 1000 || 0.033); S.last = now;
    if (!S.anim && now > S.activeUntil) return;       // animations off: render only while a new emotion settles
    if (S.anim) S.t += dt;
    const prof = S.prof || profile('idle'), k = 1 - Math.exp(-dt * 8), tg = bodyTarget(prof);
    for (const key of ['dx', 'dy', 'rot', 'sc']) S.cur[key] = lerp(S.cur[key], tg[key], k);
    S.mouth = lerp(S.mouth, mouthTarget(prof), 1 - Math.exp(-dt * 20));
    try { S.adapter.frame(dt, prof, blinkOpen(dt, prof), S.mouth, S.cur); } catch (e) { fail(e); S.adapter = null; return; }
    if (now - S.maskAt > 200) { S.maskAt = now; sendMask(); }
  }
  requestAnimationFrame(tick);
  window.addEventListener('resize', () => { if (S.adapter && S.adapter.resize) S.adapter.resize(); S.activeUntil = performance.now() + 500; });

  async function init(m) {
    try {
      S.profiles = m.profiles || {}; S.fps = m.fps || 30; S.anim = m.anim !== false;
      S.adapter = m.engine === 'live2d' ? await initLive2D(m) : await initVRM(m);
      if (S.adapter.autofit) S.adapter.autofit();
      setEmotion(m.emotion || 'idle');
      emit({ ev: 'loaded', info: S.adapter.info });
    } catch (e) { fail(e); }
  }
  window.vw = {
    cmd(m) {
      if (m.cmd === 'init') init(m);
      else if (m.cmd === 'emotion') setEmotion(m.name);
      else if (m.cmd === 'config') { if (m.fps) S.fps = Math.max(5, Math.min(60, m.fps)); if ('anim' in m) { S.anim = !!m.anim; S.activeUntil = performance.now() + 2500; } }
      else if (m.cmd === 'pause') S.paused = !!m.value;
      else if (m.cmd === 'mouth') { S.ext = m.level === null ? null : Math.max(0, Math.min(1, +m.level || 0)); S.extAt = performance.now(); S.activeUntil = S.extAt + 500; }
    },
  };
  emit({ ev: 'page' });
})();
