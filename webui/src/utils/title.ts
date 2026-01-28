export function setDocumentTitle(title?: string) {
  document.title = title ? `gptme - ${title}` : 'gptme';
}
