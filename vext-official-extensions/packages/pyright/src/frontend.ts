export function mount(container: HTMLElement): void {
  container.replaceChildren();
  const title = document.createElement("h2");
  title.textContent = 'Pyright';
  container.append(title);
}
