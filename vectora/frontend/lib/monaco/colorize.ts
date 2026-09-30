export async function colorizeLines(
  text: string,
  languageId: string,
): Promise<string[] | null> {
  try {
    const { default: monaco } = await import("monaco-editor");
    const { prepareMonaco } = await import("./setup");
    prepareMonaco(monaco);
    const html = await monaco.editor.colorize(text, languageId, { tabSize: 2 });
    return html.split("\n");
  } catch {
    return null;
  }
}
