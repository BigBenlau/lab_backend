const buttons = [...document.querySelectorAll('.concept-button')];
const pages = [...document.querySelectorAll('.concept-page')];
const jumpButtons = [...document.querySelectorAll('[data-jump]')];
const validConcepts = new Set(pages.map((page) => page.dataset.page));

function selectConcept(name, updateHistory = true) {
  if (name === 'human') {
    location.replace('./lab.html#home');
    return;
  }
  if (!validConcepts.has(name)) return;

  document.body.dataset.concept = name;
  pages.forEach((page) => {
    const active = page.dataset.page === name;
    page.classList.toggle('is-visible', active);
    page.hidden = !active;
  });
  buttons.forEach((button) => {
    const active = button.dataset.target === name;
    button.classList.toggle('is-active', active);
    button.setAttribute('aria-selected', String(active));
  });

  if (updateHistory) history.replaceState(null, '', `#${name}`);
  window.scrollTo({ top: 0, behavior: 'instant' });
}

buttons.forEach((button) => {
  button.addEventListener('click', () => selectConcept(button.dataset.target));
});

jumpButtons.forEach((button) => {
  button.addEventListener('click', (event) => {
    event.preventDefault();
    selectConcept(button.dataset.jump);
  });
});

document.querySelector('.switcher-buttons').addEventListener('keydown', (event) => {
  if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
  event.preventDefault();
  const current = buttons.findIndex((button) => button.classList.contains('is-active'));
  const direction = event.key === 'ArrowRight' ? 1 : -1;
  const next = (current + direction + buttons.length) % buttons.length;
  buttons[next].focus();
  selectConcept(buttons[next].dataset.target);
});

window.addEventListener('hashchange', () => {
  selectConcept(location.hash.slice(1) || 'overview', false);
});

selectConcept(validConcepts.has(location.hash.slice(1)) ? location.hash.slice(1) : 'human', false);
