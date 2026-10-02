// Stand-in scene: a watercolor sun rising over hills, with a runner drawn as one ink line.
let paper, runner;
function setup(ctx, S) {
  paper = paperLayer(S);
  runner = [];
  for (let i = 0; i <= 400; i++) {  // a looping gesture line for a running figure
    const u = i / 400 * U.TAU * 2;
    runner.push([S.W * 0.5 + Math.sin(u) * 60 + Math.sin(u * 3) * 25 + i * 0.8 - 160, S.H * 0.55 + Math.cos(u * 1.5) * 90 + U.noise2(i * 0.02, 3) * 18]);
  }
}
function draw(ctx, t, S, A) {
  const p = t / S.duration;
  ctx.drawImage(paper, 0, 0);
  const rise = U.ease.out(U.clamp(p * 1.4));
  wash(ctx, S.W * 0.62, S.H * (0.62 - 0.22 * rise), S.H * 0.16, S.palette[S.palette.length - 2], 0.9, 1, 1 + 0.04 * A.beat * S.intensity);
  wash(ctx, S.W * 0.3, S.H * 0.92, S.W * 0.42, S.palette[1], 0.7, 4);
  wash(ctx, S.W * 0.78, S.H * 0.98, S.W * 0.35, S.palette[2], 0.6, 9);
  inkPath(ctx, runner, U.smoothstep(0.1, 0.85, p), U.rgba('#2a2420', 0.85), 4 + 2 * A.beat);
}
