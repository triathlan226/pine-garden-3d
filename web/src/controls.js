// Input: keyboard + mouse drag / pointer lock on desktop; virtual joystick + right-half swipe on touch.
export class Controls {
  constructor(canvas, joystickEl) {
    this.canvas = canvas;
    this.keys = new Set();
    this.move = { x: 0, y: 0 };     // strafe, forward  (-1..1)
    this.look = { dx: 0, dy: 0 };   // accumulated look delta (radians)
    this.fast = false;
    this.touch = matchMedia('(pointer: coarse)').matches || 'ontouchstart' in window;
    this.listeners = {};
    this.enabled = true;
    this.joy = joystickEl;
    this.joyVec = { x: 0, y: 0 };
    this._bind();
  }

  on(ev, fn) { (this.listeners[ev] = this.listeners[ev] || []).push(fn); }
  emit(ev, ...a) { (this.listeners[ev] || []).forEach((f) => f(...a)); }

  _bind() {
    const c = this.canvas;
    addEventListener('keydown', (e) => {
      if (e.target.closest && e.target.closest('input, textarea')) return;
      const k = e.key.toLowerCase();
      this.keys.add(k);
      if (k === 'shift') this.fast = true;
      if (['w', 'a', 's', 'd', 'arrowup', 'arrowdown', 'arrowleft', 'arrowright'].includes(k)) this.emit('user-move');
      if (k === 'e' || k === 'enter') this.emit('interact');
      if (k === 't') this.emit('toggle-tour');
      if (k === 'm') this.emit('toggle-map');
      if (k === 'escape') this.emit('escape');
      if (k.startsWith('arrow') || k === ' ') e.preventDefault();
    });
    addEventListener('keyup', (e) => {
      const k = e.key.toLowerCase();
      this.keys.delete(k);
      if (k === 'shift') this.fast = false;
    });
    addEventListener('blur', () => { this.keys.clear(); this.fast = false; });

    // mouse / pen: drag to look, click to pick
    let drag = null;
    c.addEventListener('pointerdown', (e) => {
      if (e.pointerType === 'touch') return;
      drag = { x: e.clientX, y: e.clientY, moved: 0 };
      c.setPointerCapture(e.pointerId);
    });
    c.addEventListener('pointermove', (e) => {
      if (e.pointerType === 'touch') return;
      if (document.pointerLockElement === c) {
        this.look.dx += e.movementX * 0.0024;
        this.look.dy += e.movementY * 0.0024;
        return;
      }
      if (!drag) return;
      const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
      drag.moved += Math.abs(dx) + Math.abs(dy);
      drag.x = e.clientX; drag.y = e.clientY;
      this.look.dx += dx * 0.0042;
      this.look.dy += dy * 0.0042;
      if (drag.moved > 6) this.emit('user-look');
    });
    c.addEventListener('pointerup', (e) => {
      if (e.pointerType === 'touch') return;
      if (drag && drag.moved < 6) this.emit('pick', e.clientX, e.clientY);
      drag = null;
    });
    c.addEventListener('dblclick', () => { if (!this.touch && c.requestPointerLock) c.requestPointerLock(); });

    // touch: right half = look, taps = pick
    const looks = new Map();
    c.addEventListener('touchstart', (e) => {
      for (const t of e.changedTouches) looks.set(t.identifier, { x: t.clientX, y: t.clientY, sx: t.clientX, sy: t.clientY, t0: performance.now(), look: t.clientX > innerWidth * 0.4 });
      e.preventDefault();
    }, { passive: false });
    c.addEventListener('touchmove', (e) => {
      for (const t of e.changedTouches) {
        const s = looks.get(t.identifier);
        if (!s || !s.look) continue;
        this.look.dx += (t.clientX - s.x) * 0.006;
        this.look.dy += (t.clientY - s.y) * 0.006;
        s.x = t.clientX; s.y = t.clientY;
        if (Math.hypot(t.clientX - s.sx, t.clientY - s.sy) > 10) this.emit('user-look');
      }
      e.preventDefault();
    }, { passive: false });
    c.addEventListener('touchend', (e) => {
      for (const t of e.changedTouches) {
        const s = looks.get(t.identifier);
        looks.delete(t.identifier);
        if (s && Math.hypot(t.clientX - s.sx, t.clientY - s.sy) < 12 && performance.now() - s.t0 < 350) this.emit('pick', t.clientX, t.clientY);
      }
    });

    // joystick
    const j = this.joy;
    if (j) {
      const knob = j.querySelector('.knob');
      let id = null, cx = 0, cy = 0, R = 50;
      const set = (x, y) => {
        let dx = x - cx, dy = y - cy;
        const d = Math.hypot(dx, dy);
        if (d > R) { dx *= R / d; dy *= R / d; }
        knob.style.transform = `translate(${dx}px, ${dy}px)`;
        this.joyVec.x = dx / R; this.joyVec.y = -dy / R;
        if (d > 8) this.emit('user-move');
      };
      j.addEventListener('pointerdown', (e) => {
        id = e.pointerId; j.setPointerCapture(id);
        const r = j.getBoundingClientRect(); cx = r.left + r.width / 2; cy = r.top + r.height / 2; R = r.width * 0.4;
        set(e.clientX, e.clientY); e.preventDefault();
      });
      j.addEventListener('pointermove', (e) => { if (e.pointerId === id) set(e.clientX, e.clientY); });
      const end = (e) => { if (e.pointerId !== id) return; id = null; this.joyVec.x = this.joyVec.y = 0; knob.style.transform = ''; };
      j.addEventListener('pointerup', end);
      j.addEventListener('pointercancel', end);
    }
  }

  /** Read movement axes for this frame. */
  sample() {
    const k = this.keys;
    let x = 0, y = 0;
    if (k.has('w') || k.has('arrowup')) y += 1;
    if (k.has('s') || k.has('arrowdown')) y -= 1;
    if (k.has('a') || k.has('arrowleft')) x -= 1;
    if (k.has('d') || k.has('arrowright')) x += 1;
    x += this.joyVec.x; y += this.joyVec.y;
    const l = Math.hypot(x, y);
    if (l > 1) { x /= l; y /= l; }
    this.move.x = x; this.move.y = y;
    const look = { dx: this.look.dx, dy: this.look.dy };
    this.look.dx = this.look.dy = 0;
    const joyFast = Math.hypot(this.joyVec.x, this.joyVec.y) > 0.97;
    return { x, y, fast: this.fast || joyFast, look, active: l > 0.05 };
  }
}
