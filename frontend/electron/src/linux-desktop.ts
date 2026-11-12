import * as fs from "node:fs";
import * as path from "node:path";

export const DEVELOPMENT_DESKTOP_NAME = "company.vectora.desktop.dev.desktop";

function desktopString(value: string): string {
  if (/[\r\n\0]/.test(value)) throw new Error("Invalid desktop entry path");
  return value.replaceAll("\\", "\\\\");
}

function execArgument(value: string): string {
  if (value.includes("="))
    throw new Error("Unsupported desktop executable path");
  const escaped = value
    .replace(/[\\"`$]/g, (char) => `\\${char}`)
    .replaceAll("%", "%%");
  return `"${desktopString(escaped)}"`;
}

/** Registra a identidade Wayland/X11 do checkout sem substituir o app instalado. */
export function registerDevelopmentDesktop(options: {
  dataHome: string;
  executable: string;
  main: string;
  icon: string;
}): void {
  if (!path.isAbsolute(options.dataHome) || !path.isAbsolute(options.icon)) {
    throw new Error("Desktop paths must be absolute");
  }
  const applications = path.join(options.dataHome, "applications");
  const content = [
    "[Desktop Entry]",
    "Type=Application",
    "Name=Vectora",
    `Exec=${execArgument(options.executable)} ${execArgument(options.main)}`,
    `Icon=${desktopString(options.icon)}`,
    `StartupWMClass=${DEVELOPMENT_DESKTOP_NAME.replace(/\.desktop$/, "")}`,
    "Terminal=false",
    "NoDisplay=true",
    "Categories=Development;IDE;",
    "",
  ].join("\n");
  fs.mkdirSync(applications, { recursive: true });
  fs.writeFileSync(path.join(applications, DEVELOPMENT_DESKTOP_NAME), content);
}
