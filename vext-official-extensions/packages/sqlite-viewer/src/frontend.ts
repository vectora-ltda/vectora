export function mount(container: HTMLElement): void {
  container.replaceChildren();
  const title = document.createElement("h2");
  title.textContent = 'SQLite Viewer';
  container.append(title);
}
