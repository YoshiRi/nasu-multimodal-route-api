// Browsers only let a page open a tab right after a click. Route plans
// take ~10s with walking routes, long past that, so opening the results
// tab once the response arrived was silently blocked. Open it at click
// time instead, show a placeholder, and point it at the page when done.

function openPendingTab() {
  const tab = window.open("", "_blank");
  if (tab) {
    tab.document.title = "読み込み中...";
    tab.document.body.textContent = "検索中です。このままお待ちください...";
  }
  return {
    show(page) {
      // Blocked anyway (or closed by the person): fall back to this tab.
      if (tab && !tab.closed) tab.location.href = page;
      else window.location.href = page;
    },
    cancel() {
      if (tab && !tab.closed) tab.close();
    },
  };
}
