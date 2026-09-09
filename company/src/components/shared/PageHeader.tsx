/**
 * PageHeader — título (e subtítulo) padrão das páginas internas da company.
 *
 * Tipografia alinhada à home: título semibold `text-2xl sm:text-3xl` no
 * `foreground`, subtítulo `muted-foreground`. Centralizado por padrão.
 */

interface PageHeaderProps {
  title: string;
  subtitle?: string;
  align?: "left" | "center";
  children?: React.ReactNode;
}

export default function PageHeader({
  title,
  subtitle,
  align = "center",
  children,
}: PageHeaderProps) {
  const alignment =
    align === "center" ? "items-center text-center" : "items-start text-start";
  return (
    <div className={`flex flex-col gap-3 ${alignment}`}>
      <h1 className="text-2xl font-semibold leading-tight tracking-[-0.5px] text-foreground sm:text-3xl">
        {title}
      </h1>
      {subtitle && (
        <p className="max-w-[640px] text-sm leading-relaxed text-muted-foreground sm:text-base">
          {subtitle}
        </p>
      )}
      {children}
    </div>
  );
}
