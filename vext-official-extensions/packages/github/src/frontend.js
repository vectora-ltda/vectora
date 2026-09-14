export function mount(container) {
    container.replaceChildren();
    const title = document.createElement("h2");
    title.textContent = "GitHub";
    container.append(title);
}
