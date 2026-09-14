export function mount(container) {
    container.replaceChildren();
    const title = document.createElement("h2");
    title.textContent = "Docker";
    container.append(title);
}
