export interface VextTheme {
  colorScheme: "light" | "dark";
  reducedMotion: boolean;
}
export type VextCommand = { id: string; title: string; run(): Promise<void> };
