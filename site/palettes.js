/* Temporary color preview. Independent of the light/dark preference. */
(() => {
  const key = 'datawork-palette';
  const names = {studio:'Stüdyo',citrus:'Narenciye',coast:'Riviera',pop:'Pop',terra:'Terra'};
  const normalize = value => Object.hasOwn(names, value) ? value : 'studio';
  let selected = 'studio';
  try { selected = normalize(localStorage.getItem(key)); } catch {}
  function apply() {
    document.documentElement.dataset.palette = selected;
    document.querySelectorAll('[data-palette-choice]').forEach(button => {
      button.setAttribute('aria-pressed', String(button.dataset.paletteChoice === selected));
    });
    const status = document.querySelector('#paletteStatus');
    if (status) status.textContent = names[selected] + ' seçili';
  }
  apply();
  window.addEventListener('storage', event => {
    if (event.key === key || event.key === null) { selected = normalize(event.newValue); apply(); }
  });
  document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('[data-palette-choice]').forEach(button => {
      button.addEventListener('click', () => {
        selected = normalize(button.dataset.paletteChoice);
        try { localStorage.setItem(key, selected); } catch {}
        apply();
      });
    });
    document.querySelector('#palettePicker').open = !window.matchMedia('(max-width: 1000px)').matches;
    apply();
  });
})();
