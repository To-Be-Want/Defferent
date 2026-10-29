document.addEventListener('DOMContentLoaded', () => {
  const header = document.querySelector('.container>.header');
  const container = document.querySelector('.app-dashboard>.container');
  if (!header || !container) return;
  const updateHeaderHeight = () => container.style.setProperty('--workspace-header-height', `${Math.ceil(header.getBoundingClientRect().height)}px`);
  const observer = new ResizeObserver(updateHeaderHeight);
  observer.observe(header);
  updateHeaderHeight();
  document.querySelectorAll('[data-open-control]').forEach(button => {
    button.addEventListener('click', () => {
      document.getElementById(button.dataset.openControl)?.click();
    });
  });
  window.addEventListener('pagehide', () => observer.disconnect());
});
