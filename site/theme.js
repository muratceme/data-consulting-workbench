/* Apply before CSS paints; explicit preferences survive reloads and OS changes. */
(() => {
  const key = 'datawork-theme';
  const root = document.documentElement;
  const media = window.matchMedia('(prefers-color-scheme: dark)');
  const modes = ['light', 'dark', 'system'];
  const normalize = value => modes.includes(value) ? value : 'system';
  let mode = 'system';
  try { mode = normalize(localStorage.getItem(key)); } catch {}

  function apply() {
    root.dataset.themeMode = mode;
    root.dataset.theme = mode === 'system' ? (media.matches ? 'dark' : 'light') : mode;
    root.style.colorScheme = root.dataset.theme;
    document.querySelectorAll('[data-theme-choice]').forEach(button => {
      button.setAttribute('aria-pressed', String(button.dataset.themeChoice === mode));
    });
  }

  apply();
  media.addEventListener('change', () => { if (mode === 'system') apply(); });
  window.addEventListener('storage', event => {
    if (event.key === key || event.key === null) { mode = normalize(event.newValue); apply(); }
  });
  document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('[data-theme-choice]').forEach(button => {
      button.addEventListener('click', () => {
        mode = normalize(button.dataset.themeChoice);
        try { localStorage.setItem(key, mode); } catch {}
        apply();
      });
    });
    apply();
  });
})();
