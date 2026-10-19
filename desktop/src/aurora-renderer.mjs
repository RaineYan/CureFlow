// Deterministic, smoothly varying noise gives the curtain irregular folds.
// This renderer intentionally has no stroked curves or periodic wave outline.
const mix = (a, b, t) => a + (b - a) * t;
const smooth = x => x * x * (3 - 2 * x);
function hash(x, y) {
  const n = Math.sin(x * 127.1 + y * 311.7) * 43758.5453;
  return n - Math.floor(n);
}
function noise(x, y) {
  const ix = Math.floor(x), iy = Math.floor(y);
  const fx = smooth(x - ix), fy = smooth(y - iy);
  return mix(mix(hash(ix, iy), hash(ix + 1, iy), fx),
    mix(hash(ix, iy + 1), hash(ix + 1, iy + 1), fx), fy);
}
function field(x, t) {
  return noise(x, t) * .62 + noise(x * 2.1 + 9, t * 1.3) * .26
    + noise(x * 4.3 + 32, t * .8) * .12;
}

export function paintAurora(context, width, height, seconds) {
  context.clearRect(0, 0, width, height);
  context.globalCompositeOperation = "lighter";
  const t = seconds * .24;
  for (let layer = 1; layer >= 0; layer--) {
    const seed = layer * 19.7;
    for (let x = 0; x < width; x += 1.8) {
      const u = x / width;
      const drift = t + seed;
      const fold = field(u * 5.2 + noise(u * 3 + seed, drift) * 2.4, drift);
      // Fold back on itself in places, with local creases and varying heights.
      const crease = Math.abs(noise(u * 12 + fold * 3, drift * .8) - .5) * 2;
      const base = height * (.38 + fold * .48 - crease * .18 + layer * .09);
      const plume = height * (.22 + field(u * 7 + seed, drift + 8) * .58);
      const envelope = Math.pow(Math.sin(Math.PI * u), 1.5);
      const clusters = Math.pow(noise(u * 5 + seed, drift + 12), 1.3);
      const ray = noise(x * .17 + seed, drift * 2);
      const softRay = noise(x * .055 + seed, drift + fold * 3);
      const opacity = envelope * (.35 + .65 * clusters)
        * (.5 + .5 * ray) * (.7 + .3 * softRay) * (layer ? .5 : 1.1);
      const gradient = context.createLinearGradient(x, base - plume, x, base + height * .045);
      gradient.addColorStop(0, "rgba(66,100,205,0)");
      gradient.addColorStop(.23, `rgba(72,123,219,${opacity * .06})`);
      gradient.addColorStop(.53, `rgba(40,170,188,${opacity * .22})`);
      gradient.addColorStop(.76, `rgba(40,211,159,${opacity * .55})`);
      gradient.addColorStop(.89, `rgba(90,240,171,${opacity * .9})`);
      gradient.addColorStop(.95, `rgba(116,248,182,${opacity * .52})`);
      gradient.addColorStop(1, "rgba(58,202,166,0)");
      context.fillStyle = gradient;
      context.fillRect(x, base - plume, 2.15, plume + height * .045);
    }
  }
  context.globalCompositeOperation = "source-over";
}
