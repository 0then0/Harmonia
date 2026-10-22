'use strict';
// Preferences only. Operator/provider credentials are never persisted.
try {
  const preference = localStorage.getItem('harmonia-theme');
  if (['system', 'light', 'dark'].includes(preference)) {
    document.documentElement.dataset.theme = preference;
  }
} catch (_) { /* Storage can be disabled; system preference remains usable. */ }
