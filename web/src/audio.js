// Ambient sound, synthesised with Web Audio (no audio files): pine wind, birdsong, and sea waves
// that fade in on the 2F balcony / sea-view deck. Starts muted; created on first user gesture.
export class Ambience {
  constructor() {
    this.ctx = null;
    this.on = false;
    this.seaTarget = 0;
  }

  _noise(ctx, seconds = 4, color = 'pink') {
    const n = ctx.sampleRate * seconds;
    const buf = ctx.createBuffer(1, n, ctx.sampleRate);
    const d = buf.getChannelData(0);
    let b0 = 0, b1 = 0, b2 = 0, last = 0;
    for (let i = 0; i < n; i++) {
      const w = Math.random() * 2 - 1;
      if (color === 'brown') { last = (last + 0.02 * w) / 1.02; d[i] = last * 3.5; }
      else { b0 = 0.997 * b0 + w * 0.029591; b1 = 0.985 * b1 + w * 0.032534; b2 = 0.95 * b2 + w * 0.048056; d[i] = (b0 + b1 + b2 + w * 0.05) * 0.9; }
    }
    const src = ctx.createBufferSource();
    src.buffer = buf; src.loop = true;
    return src;
  }

  _init() {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    this.ctx = ctx;
    this.master = ctx.createGain();
    this.master.gain.value = 0;
    this.master.connect(ctx.destination);
    // wind in the pines: band-passed pink noise with slow gusts
    const wind = this._noise(ctx, 6, 'pink');
    const bp = ctx.createBiquadFilter(); bp.type = 'bandpass'; bp.frequency.value = 520; bp.Q.value = 0.6;
    const hs = ctx.createBiquadFilter(); hs.type = 'highshelf'; hs.frequency.value = 2500; hs.gain.value = 4;
    this.windGain = ctx.createGain(); this.windGain.gain.value = 0.35;
    wind.connect(bp).connect(hs).connect(this.windGain).connect(this.master);
    const lfo = ctx.createOscillator(); lfo.frequency.value = 0.07;
    const lfoG = ctx.createGain(); lfoG.gain.value = 0.18;
    lfo.connect(lfoG).connect(this.windGain.gain);
    const lfo2 = ctx.createOscillator(); lfo2.frequency.value = 0.11;
    const lfo2G = ctx.createGain(); lfo2G.gain.value = 260;
    lfo2.connect(lfo2G).connect(bp.frequency);
    wind.start(); lfo.start(); lfo2.start();
    // sea: brown noise, low-passed, swelling every ~9 s
    const sea = this._noise(ctx, 8, 'brown');
    const lp = ctx.createBiquadFilter(); lp.type = 'lowpass'; lp.frequency.value = 650;
    const swell = ctx.createGain(); swell.gain.value = 0.5;
    const sl = ctx.createOscillator(); sl.frequency.value = 0.11;
    const slG = ctx.createGain(); slG.gain.value = 0.4;
    sl.connect(slG).connect(swell.gain);
    this.seaGain = ctx.createGain(); this.seaGain.gain.value = 0;
    sea.connect(lp).connect(swell).connect(this.seaGain).connect(this.master);
    sea.start(); sl.start();
    this._birds();
  }

  _chirp(t0, pan) {
    const ctx = this.ctx;
    const o = ctx.createOscillator(); o.type = 'sine';
    const g = ctx.createGain(); g.gain.value = 0;
    const p = ctx.createStereoPanner ? ctx.createStereoPanner() : null;
    const base = 2600 + Math.random() * 1800;
    const notes = 2 + Math.floor(Math.random() * 4);
    let t = t0;
    for (let i = 0; i < notes; i++) {
      const dur = 0.07 + Math.random() * 0.09;
      o.frequency.setValueAtTime(base * (1 + Math.random() * 0.25), t);
      o.frequency.exponentialRampToValueAtTime(base * (0.75 + Math.random() * 0.6), t + dur);
      g.gain.setValueAtTime(0, t);
      g.gain.linearRampToValueAtTime(0.05 + Math.random() * 0.04, t + 0.012);
      g.gain.exponentialRampToValueAtTime(0.0008, t + dur);
      t += dur + 0.04 + Math.random() * 0.08;
    }
    if (p) { p.pan.value = pan; o.connect(g).connect(p).connect(this.master); } else o.connect(g).connect(this.master);
    o.start(t0); o.stop(t + 0.1);
  }

  _birds() {
    const loop = () => {
      if (this.ctx && this.on) this._chirp(this.ctx.currentTime + 0.05, Math.random() * 1.6 - 0.8);
      this._birdT = setTimeout(loop, 1800 + Math.random() * 5200);
    };
    loop();
  }

  async toggle(v = !this.on) {
    if (!this.ctx) this._init();
    if (this.ctx.state === 'suspended') await this.ctx.resume();
    this.on = v;
    const now = this.ctx.currentTime;
    this.master.gain.cancelScheduledValues(now);
    this.master.gain.setTargetAtTime(v ? 0.9 : 0, now, 0.4);
    return this.on;
  }

  /** seaAmount 0..1 from the player's position (balcony / sea deck). */
  update(seaAmount, windAmount = 1) {
    if (!this.ctx || !this.on) return;
    const now = this.ctx.currentTime;
    this.seaGain.gain.setTargetAtTime(seaAmount * 0.55, now, 1.2);
    this.windGain.gain.setTargetAtTime(0.3 * windAmount, now, 1.0);
  }
}
