document.addEventListener('DOMContentLoaded', () => {
  const preview = document.getElementById('dag-preview');
  const button = document.getElementById('dag-preview-expand');
  const graph = document.getElementById('dag-svg');
  if (!preview || !button || !graph) return;
  let frame;
  let fitTimer;
  function fitGraph() {
    if (!graph.getClientRects().length || !graph.querySelector('.node') || typeof zoom === 'undefined' || !zoom) return;
    const group = graph.querySelector('g');
    if (!group || !graph.clientWidth || !graph.clientHeight) return;
    const bounds = group.getBBox();
    if (!bounds.width || !bounds.height) return;
    const scale = Math.min(1, Math.max(1, graph.clientWidth - 48) / bounds.width,
      Math.max(1, graph.clientHeight - 48) / bounds.height);
    const transform = d3.zoomIdentity.translate(
      graph.clientWidth / 2 - (bounds.x + bounds.width / 2) * scale,
      graph.clientHeight / 2 - (bounds.y + bounds.height / 2) * scale).scale(scale);
    d3.select(graph).call(zoom.transform, transform);
  }
  const scheduleFit = () => {
    clearTimeout(fitTimer);
    // Existing DAG nodes animate to their new positions for 300 ms.
    fitTimer = setTimeout(fitGraph, 360);
  };
  const resize = () => {
    cancelAnimationFrame(frame);
    frame = requestAnimationFrame(() => {
      if (typeof handleResize === 'function') handleResize();
      scheduleFit();
    });
  };
  const label = () => {
    button.textContent = preview.classList.contains('expanded') ? '返回小图' : (window.innerWidth <= 768 ? '展开查看' : '放大查看');
  };
  function expand(value) {
    preview.classList.toggle('expanded', value);
    button.setAttribute('aria-expanded', String(value));
    if (value) {
      preview.setAttribute('role', 'dialog');
      preview.setAttribute('aria-modal', 'true');
    } else {
      preview.removeAttribute('role');
      preview.removeAttribute('aria-modal');
    }
    label();
    resize();
    button.focus();
  }
  button.onclick = () => expand(!preview.classList.contains('expanded'));
  document.addEventListener('keydown', event => {
    if (!preview.classList.contains('expanded')) return;
    if (event.key === 'Escape') { event.preventDefault(); expand(false); }
    if (event.key === 'Tab') {
      const items = [...preview.querySelectorAll('button,a,[tabindex="0"]')].filter(node => node.getClientRects().length);
      const first = items[0], last = items[items.length-1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }
  });
  const updateEmpty = () => { preview.dataset.empty = String(!graph.querySelector('.node')); scheduleFit(); };
  const mutation = new MutationObserver(updateEmpty);
  mutation.observe(graph, {childList:true, subtree:true});
  const observer = new ResizeObserver(resize);
  observer.observe(graph);
  window.addEventListener('resize', () => { label(); resize(); });
  updateEmpty();
  label();
  window.addEventListener('pagehide', () => { observer.disconnect(); mutation.disconnect(); cancelAnimationFrame(frame); clearTimeout(fitTimer); });
});
