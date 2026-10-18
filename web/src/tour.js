import * as THREE from 'three';
import { planToWorld } from './content.js';

// Auto tour: drives the same capsule controller as free roaming (so it obeys collisions),
// walking waypoint to waypoint and pausing at hotspots.
export class Tour {
  constructor({ player, hotspots, content, reducedMotion }) {
    this.player = player;
    this.hotspots = hotspots;
    this.reduced = reducedMotion;
    this.speed = content.tour.walkSpeed || 1.3;
    this.start = new THREE.Vector3(...planToWorld(content.tour.start));
    this.points = content.tour.points.map((q) => ({ ...q, w: new THREE.Vector3(...planToWorld(q.p)) }));
    this.active = false;
    this.listeners = {};
    this.stats = { stuck: 0, stops: [], startedAt: 0, simTime: 0 };
  }

  on(ev, fn) { (this.listeners[ev] = this.listeners[ev] || []).push(fn); }
  emit(ev, ...a) { (this.listeners[ev] || []).forEach((f) => f(...a)); }

  begin(opts = {}) {
    this.timeScale = opts.timeScale || 1;
    this.dwellOverride = opts.dwell;
    this.i = opts.from || 0;
    this.state = 'walk';
    this.wait = 0;
    this.active = true;
    this.stats = { stuck: 0, stops: [], startedAt: performance.now(), simTime: 0, minClear: Infinity, maxPushes: 0 };
    this._prog = { t: 0, d: Infinity };
    if (opts.fromStart !== false && this.i === 0) {
      const p = this.start;
      const dir = new THREE.Vector3().subVectors(this.points[0].w, p);
      this.player.teleport(p.x, p.y, p.z, Math.atan2(-dir.x, -dir.z));
    }
    this.emit('start');
  }

  stop(reason = 'user') {
    if (!this.active) return;
    this.active = false;
    this.emit('stop', reason);
  }

  skipWait() { if (this.state === 'dwell') this.wait = 0; }

  dwellFor(h) {
    if (this.dwellOverride !== undefined) return this.dwellOverride;
    return h.dwell || Math.max(12, Math.min(22, 8 + h.body.length / 22));
  }

  /** Returns {wish: Vector3, yawTarget|null}. dt is real seconds. */
  update(dt) {
    const out = { wish: new THREE.Vector3(), yawTarget: null, fast: false };
    if (!this.active) return out;
    const sdt = dt * this.timeScale;
    this.stats.simTime += sdt;
    const pl = this.player;
    const pt = this.points[this.i];
    if (!pt) { this.finish(); return out; }
    if (this.state === 'walk') {
      if (this.reduced) {
        // reduced motion: jump straight to the next stop
        let j = this.i;
        while (j < this.points.length - 1 && !this.points[j].stop) j++;
        const q = this.points[j].w;
        pl.teleport(q.x, q.y + 0.05, q.z);
        this.i = j;
      }
      const to = new THREE.Vector3(pt.w.x - pl.pos.x, 0, pt.w.z - pl.pos.z);
      const d = to.length();
      if (d < 0.35) {
        if (pt.stop) {
          this.state = 'turn';
          this.turnT = 0;
          const it = this.hotspots.byId(pt.stop);
          const lk = it ? it.look : pt.w;
          this.stopYaw = Math.atan2(-(lk.x - pl.pos.x), -(lk.z - pl.pos.z));
          const eyeY = pl.pos.y + 1.6;
          this.stopPitch = Math.atan2(lk.y - eyeY, Math.hypot(lk.x - pl.pos.x, lk.z - pl.pos.z));
        } else {
          this.i++;
        }
        this._prog = { t: 0, d: Infinity };
        return out;
      }
      to.normalize();
      const sp = this.speed * this.timeScale;
      out.wish.copy(to).multiplyScalar(Math.min(sp, d / Math.max(sdt, 1e-3) * 0.9 * this.timeScale));
      out.yawTarget = Math.atan2(-to.x, -to.z);
      out.pitchTarget = (pt.w.y - pl.pos.y) > 0.6 ? 0.18 : 0;
      // stuck detection (in simulated time)
      this._prog.t += sdt;
      if (d < this._prog.d - 0.15) this._prog = { t: 0, d };
      if (this._prog.t > 3.5) {
        this.stats.stuck++;
        (this.stats.stuckAt = this.stats.stuckAt || []).push({ i: this.i, pos: pl.pos.toArray().map((v) => +v.toFixed(2)), to: pt.p });
        this.emit('stuck', this.i, pl.pos.clone());
        pl.teleport(pt.w.x, pt.w.y + 0.05, pt.w.z);
        this._prog = { t: 0, d: Infinity };
      }
      const c = pl.clearance();
      this.stats.minClear = Math.min(this.stats.minClear, c);
    } else if (this.state === 'turn') {
      this.turnT += sdt;
      out.yawTarget = this.stopYaw;
      out.pitchTarget = THREE.MathUtils.clamp(this.stopPitch, -0.4, 0.35);
      if (this.turnT > 0.9 || this.reduced) {
        const id = this.points[this.i].stop;
        this.hotspots.show(id);
        const it = this.hotspots.byId(id);
        this.wait = this.dwellFor(it.h);
        this.state = 'dwell';
        this.stats.stops.push({ id, at: this.stats.simTime, pos: pl.pos.toArray().map((v) => +v.toFixed(2)) });
        this.emit('stop-at', id, this.i);
      }
    } else if (this.state === 'dwell') {
      out.yawTarget = this.stopYaw;
      out.pitchTarget = THREE.MathUtils.clamp(this.stopPitch, -0.4, 0.35);
      this.wait -= sdt;
      if (this.wait <= 0) {
        this.i++;
        if (this.i >= this.points.length) { this.finish(); return out; }
        this.hotspots.hide();
        this.state = 'walk';
      }
    }
    return out;
  }

  finish() {
    this.active = false;
    this.emit('finish', this.stats);
  }

  remaining() {
    // remaining stops
    return this.points.slice(this.i).filter((p) => p.stop).length;
  }
}
