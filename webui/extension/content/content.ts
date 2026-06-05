// content.ts — capture text selection and page context, relay to background

let lastSelection = '';

document.addEventListener('mouseup', () => {
  const sel = window.getSelection()?.toString().trim() ?? '';
  if (sel && sel !== lastSelection) {
    lastSelection = sel;
    chrome.runtime.sendMessage({
      type: 'SELECTION',
      // Truncate long selections to stay within reasonable token budgets
      selection: sel.length > 2000 ? sel.slice(0, 2000) + '…' : sel,
      url: location.href,
      title: document.title,
    }).catch(() => {
      // Panel may not be open — ignore
    });
  }
});
