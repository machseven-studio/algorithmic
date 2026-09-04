/* Ensures Chart.js exists before the legacy analytics renderer executes. */
(() => {
  if (window.Chart) return;
  const script = document.createElement('script');
  script.src = 'https://cdn.jsdelivr.net/npm/chart.js@4.5.0/dist/chart.umd.min.js';
  document.head.appendChild(script);
})();
