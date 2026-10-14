/* Apply a saved theme before first paint; dashboard.js owns the toggle. */
try {
  const theme = localStorage.getItem('gpuroster-theme');
  if (theme === 'light' || theme === 'dark') document.documentElement.dataset.theme = theme;
} catch (error) {
  // Storage can be blocked; the operating-system theme still applies.
}
