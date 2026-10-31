export function mount(container: HTMLElement): void {
  container.replaceChildren();
  const title = document.createElement("h2");
  title.textContent = 'Playwright';
  container.append(title);
}
