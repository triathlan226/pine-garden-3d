import * as THREE from 'three';

// First-person capsule controller on a three-mesh-bvh collider (pattern from the
// three-mesh-bvh "characterMovement" example). Feet position is `pos`; eye = pos + EYE.
export const EYE = 1.6;
const RADIUS = 0.3;
const GRAVITY = -24;
const SUBSTEPS = 5;

export class Player {
  constructor(collider) {
    this.collider = collider;
    this.pos = new THREE.Vector3();
    this.vel = new THREE.Vector3();
    this.onGround = false;
    this.yaw = 0;
    this.pitch = 0;
    this.segment = new THREE.Line3(new THREE.Vector3(0, RADIUS, 0), new THREE.Vector3(0, EYE + 0.15 - RADIUS, 0));
    this.lastSafe = new THREE.Vector3();
    this.falls = 0;
    this._seg = new THREE.Line3();
    this._box = new THREE.Box3();
    this._tri = new THREE.Vector3();
    this._cap = new THREE.Vector3();
    this._d = new THREE.Vector3();
    this.lastPushes = 0;
  }

  teleport(x, y, z, yaw) {
    this.pos.set(x, y, z);
    this.vel.set(0, 0, 0);
    if (yaw !== undefined) this.yaw = yaw;
    this.groundSnap();
    this.lastSafe.copy(this.pos);
  }

  /** Drop the feet onto the walkable surface right below (used after teleports). */
  groundSnap() {
    const ray = new THREE.Raycaster(new THREE.Vector3(this.pos.x, this.pos.y + 2.2, this.pos.z), new THREE.Vector3(0, -1, 0), 0, 12);
    ray.firstHitOnly = true;
    const hit = ray.intersectObject(this.collider, false)[0];
    if (hit) this.pos.y = hit.point.y + 0.02;
  }

  /** wish: desired horizontal velocity (world, m/s). */
  update(dt, wish) {
    dt = Math.min(dt, 0.1);
    const h = dt / SUBSTEPS;
    for (let i = 0; i < SUBSTEPS; i++) this._step(h, wish);
    if (this.pos.y < -12) {
      // fell out of the world: back to the last safe spot
      this.falls++;
      this.pos.copy(this.lastSafe);
      this.vel.set(0, 0, 0);
    } else if (this.onGround) {
      this.lastSafe.copy(this.pos);
    }
  }

  _step(dt, wish) {
    const accel = this.onGround ? 14 : 3;
    this.vel.x += (wish.x - this.vel.x) * Math.min(1, accel * dt);
    this.vel.z += (wish.z - this.vel.z) * Math.min(1, accel * dt);
    this.vel.y += dt * GRAVITY;
    this.pos.addScaledVector(this.vel, dt);

    const seg = this._seg.copy(this.segment);
    seg.start.add(this.pos);
    seg.end.add(this.pos);
    const box = this._box.makeEmpty();
    box.expandByPoint(seg.start).expandByPoint(seg.end);
    box.min.addScalar(-RADIUS);
    box.max.addScalar(RADIUS);
    const triPoint = this._tri, capPoint = this._cap;
    let pushes = 0;
    this.collider.geometry.boundsTree.shapecast({
      intersectsBounds: (b) => b.intersectsBox(box),
      intersectsTriangle: (tri) => {
        const dist = tri.closestPointToSegment(seg, triPoint, capPoint);
        if (dist < RADIUS) {
          const depth = RADIUS - dist;
          const dir = capPoint.sub(triPoint).normalize();
          seg.start.addScaledVector(dir, depth);
          seg.end.addScaledVector(dir, depth);
          pushes++;
        }
      },
    });
    this.lastPushes = pushes;
    const newPos = this._d.copy(seg.start).sub(this.segment.start);
    const delta = newPos.clone().sub(this.pos);
    this.onGround = delta.y > Math.abs(dt * this.vel.y * 0.25);
    const off = Math.max(0, delta.length() - 1e-5);
    delta.normalize().multiplyScalar(off);
    this.pos.add(delta);
    if (this.onGround) this.vel.y = 0;
    else if (off > 0) {
      delta.normalize();
      this.vel.addScaledVector(delta, -delta.dot(this.vel));
    }
  }

  eye(target) {
    return target.set(this.pos.x, this.pos.y + EYE, this.pos.z);
  }

  /** Minimum distance from the capsule segment to the collider (for automated wall checks). */
  clearance() {
    const seg = this._seg.copy(this.segment);
    seg.start.add(this.pos).y += 0.25;   // ignore the floor contact
    seg.end.add(this.pos);
    let best = Infinity;
    const a = new THREE.Vector3(), b = new THREE.Vector3();
    const box = new THREE.Box3().setFromPoints([seg.start, seg.end]).expandByScalar(1.0);
    this.collider.geometry.boundsTree.shapecast({
      intersectsBounds: (bb) => bb.intersectsBox(box),
      intersectsTriangle: (tri) => { best = Math.min(best, tri.closestPointToSegment(seg, a, b)); },
    });
    return best;
  }
}
