import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { Download } from "lucide-react";
import { m } from "#/paraglide/messages";
import Container from "#/components/shared/Container";
import PageHeader from "#/components/shared/PageHeader";
import { useLatestVersion } from "#/hooks/use-latest-version";

// vectora-services (Cloudflare Worker + R2). Rota pública sem token:
// GET /download/:channel/:os-:arch.:ext (services/src/updates/worker.ts).
const UPDATE_SERVER = "https://services.vectora.company";
const CHANNEL = "latest";

type OS = "windows" | "macos" | "linux";

interface Variant {
  arch: string;
  ext: string;
  label: string;
}

interface Platform {
  os: OS;
  osToken: string;
  label: string;
  emoji: string;
  primary: Variant;
  others: Variant[];
  install: () => string;
}

export function downloadHref(
  osToken: string,
  arch: string,
  ext: string,
): string {
  return `${UPDATE_SERVER}/download/${CHANNEL}/${osToken}-${arch}.${ext}`;
}

export const PLATFORMS: Platform[] = [
  {
    os: "windows",
    osToken: "win",
    label: "Windows",
    emoji: "🪟",
    primary: { arch: "x64", ext: "exe", label: ".exe (x64)" },
    others: [
      { arch: "arm64", ext: "exe", label: ".exe (ARM64)" },
      { arch: "arm64", ext: "msi", label: ".msi (ARM64)" },
      { arch: "x64", ext: "msi", label: ".msi (x64)" },
    ],
    install: m.downloads_install_windows,
  },
  {
    os: "macos",
    osToken: "mac",
    label: "macOS",
    emoji: "🍎",
    primary: { arch: "arm64", ext: "dmg", label: ".dmg (Apple Silicon)" },
    others: [{ arch: "arm64", ext: "zip", label: ".zip (Apple Silicon)" }],
    install: m.downloads_install_macos,
  },
  {
    os: "linux",
    osToken: "linux",
    label: "Linux",
    emoji: "🐧",
    primary: { arch: "x64", ext: "AppImage", label: ".AppImage (x64)" },
    others: [
      { arch: "arm64", ext: "AppImage", label: ".AppImage (ARM64)" },
      { arch: "x64", ext: "deb", label: ".deb (x64)" },
      { arch: "arm64", ext: "deb", label: ".deb (ARM64)" },
      { arch: "x64", ext: "rpm", label: ".rpm (x64)" },
      { arch: "arm64", ext: "rpm", label: ".rpm (ARM64)" },
      { arch: "x64", ext: "snap", label: ".snap (x64)" },
      { arch: "arm64", ext: "snap", label: ".snap (ARM64)" },
      { arch: "x64", ext: "pkg.tar.zst", label: ".pkg.tar.zst (x64)" },
      { arch: "arm64", ext: "pkg.tar.zst", label: ".pkg.tar.zst (ARM64)" },
      { arch: "x64", ext: "flatpak", label: ".flatpak (x64)" },
      { arch: "arm64", ext: "flatpak", label: ".flatpak (ARM64)" },
      { arch: "x64", ext: "tar.gz", label: ".tar.gz (x64)" },
      { arch: "arm64", ext: "tar.gz", label: ".tar.gz (ARM64)" },
    ],
    install: m.downloads_install_linux,
  },
];

/** Detecta o SO pelo userAgent (client-only — SSR retorna null). */
export function detectOS(): OS | null {
  if (typeof navigator === "undefined") return null;
  const ua = navigator.userAgent.toLowerCase();
  if (ua.includes("win")) return "windows";
  if (ua.includes("mac")) return "macos";
  if (ua.includes("linux") || ua.includes("x11")) return "linux";
  return null;
}

export const Route = createFileRoute("/downloads")({
  head: () => ({
    meta: [
      { title: m.page_downloads_title() },
      { name: "description", content: m.page_downloads_desc() },
    ],
  }),
  component: DownloadsPage,
});

function DownloadsPage() {
  const [userOS, setUserOS] = useState<OS | null>(null);
  // detectOS() lê navigator.userAgent — não existe no SSR, só no cliente.
  // oxlint-disable-next-line set-state-in-effect
  useEffect(() => setUserOS(detectOS()), []);
  const { data: latestVersion } = useLatestVersion();

  // O SO detectado vem primeiro (destacado como recomendado).
  const ordered = [...PLATFORMS].sort((a, b) =>
    a.os === userOS ? -1 : b.os === userOS ? 1 : 0,
  );

  return (
    <Container size="default" className="py-16">
      <PageHeader title={m.page_downloads_title()}>
        <p className="mt-2 text-muted-foreground">{m.page_downloads_desc()}</p>
        {latestVersion && (
          <p className="mt-1 text-xs text-muted-foreground">
            {m.current_version_label()}: v{latestVersion}
          </p>
        )}
      </PageHeader>

      <div className="mt-10 grid grid-cols-1 gap-4 sm:grid-cols-3">
        {ordered.map((p) => {
          const recommended = p.os === userOS;
          return (
            <div
              key={p.os}
              className={`flex flex-col gap-4 rounded-2xl border p-6 transition-all ${
                recommended
                  ? "border-primary bg-primary/5"
                  : "border-border bg-card/30"
              }`}
            >
              <div className="flex flex-col items-center gap-3 text-center">
                <span className="h-5 text-[11px] font-semibold uppercase tracking-wide text-primary">
                  {recommended ? m.downloads_recommended() : ""}
                </span>
                <span className="text-4xl leading-none" aria-hidden>
                  {p.emoji}
                </span>
                <div>
                  <p className="font-semibold text-foreground">{p.label}</p>
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    {p.primary.label}
                  </p>
                </div>
                <a
                  href={downloadHref(p.osToken, p.primary.arch, p.primary.ext)}
                  className="mt-1 inline-flex items-center gap-1.5 rounded-xl bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground hover:bg-primary/90 transition-colors"
                >
                  <Download className="h-4 w-4" />
                  {m.downloads_cta()}
                </a>
              </div>

              {p.others.length > 0 && (
                <div className="border-t border-border pt-3">
                  <p className="mb-1.5 text-center text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                    {m.downloads_other_variants()}
                  </p>
                  <div className="flex flex-wrap justify-center gap-2">
                    {p.others.map((v) => (
                      <a
                        key={`${v.arch}-${v.ext}`}
                        href={downloadHref(p.osToken, v.arch, v.ext)}
                        aria-label={`${m.downloads_cta()}: ${p.label} ${v.label}`}
                        className="rounded-lg border border-border px-2.5 py-1.5 text-xs text-muted-foreground transition-colors hover:border-primary hover:text-primary"
                      >
                        {v.label}
                      </a>
                    ))}
                  </div>
                </div>
              )}

              <p className="border-t border-border pt-3 text-xs leading-relaxed text-muted-foreground">
                {p.install()}
              </p>
            </div>
          );
        })}
      </div>

      <p className="mt-8 text-center text-xs text-muted-foreground">
        {m.downloads_footer()}
      </p>
    </Container>
  );
}
