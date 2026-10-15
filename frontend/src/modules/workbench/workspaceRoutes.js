const VIEWS = new Set(['home', 'applications', 'community', 'files', 'schedule', 'extensions', 'settings', 'data', 'explore', 'strategy-workshop', 'app-workshop']);

export function workspaceView(hash = '') {
  const name = String(hash).replace(/^#/, '').split('?')[0];
  return name === 'applications' ? 'extensions' : VIEWS.has(name) ? name : '';
}

export function workspaceHash(view, previous = '') {
  if (view === 'applications') view = 'extensions';
  if (!VIEWS.has(view) || view === 'home') return '';
  // The Explore module owns its session/run parameters.
  if (view === 'explore' && workspaceView(previous) === 'explore') return previous;
  return '#' + view;
}
