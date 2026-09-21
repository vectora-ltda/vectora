export function mount(container: HTMLElement): void {
  container.replaceChildren();
  const title = document.createElement("h2");
  title.textContent = 'Pre-commit';
  container.append(title);
}
