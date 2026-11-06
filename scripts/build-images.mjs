// Build the four production images locally for acceptance (Phase 17, D-117).
// Tags are local only (matecart-dev/<image>:acceptance-1); nothing is pushed to any registry.
import { execFileSync } from 'node:child_process';

const version = process.env.MATECART_VERSION ?? '1.0.0-acceptance.1';
const sha = execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
const images = [
  ['commerce-api', ['--target', 'prod', 'services/commerce-api']],
  ['control-api', ['--target', 'prod', 'services/control-api']],
  ['commerce-web', ['-f', 'apps/web.Dockerfile', '--build-arg', 'APP=commerce-web', '.']],
  ['platform-console', ['-f', 'apps/web.Dockerfile', '--build-arg', 'APP=platform-console', '.']],
];

for (const [name, args] of images) {
  const tag = `matecart-dev/${name}:acceptance-1`;
  console.log(`Building ${tag}`);
  execFileSync(
    'docker',
    [
      'build',
      '--build-arg',
      `MATECART_VERSION=${version}`,
      '--build-arg',
      `MATECART_GIT_SHA=${sha}`,
      '-t',
      tag,
      ...args,
    ],
    { stdio: 'inherit' },
  );
}
console.log('Production images built locally (not published).');
