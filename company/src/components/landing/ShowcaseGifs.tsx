import { useState } from "react";
import { Clock } from "lucide-react";
import { m } from "#/paraglide/messages";
import { track } from "#/lib/analytics/plausible";

interface ShowcaseCardProps {
  gif: string;
  alt: string;
  title: string;
  desc: string;
  ready: boolean;
}

function ShowcaseCard({ gif, alt, title, desc, ready }: ShowcaseCardProps) {
  const [failed, setFailed] = useState(!ready);

  return (
    <div className="overflow-hidden rounded-2xl border border-border bg-card/40">
      <div
        style={{ aspectRatio: "496 / 232" }}
        onMouseEnter={() => track("gif_viewed", { gif })}
      >
        {failed ? (
          <div className="flex h-full w-full flex-col items-center justify-center gap-2 bg-card/60 text-muted-foreground">
            <Clock className="h-6 w-6" />
            <span className="text-[13px]">{m.showcase_preview_soon()}</span>
          </div>
        ) : (
          <img
            src={gif}
            alt={alt}
            loading="lazy"
            decoding="async"
            className="h-full w-full object-cover gif-skeleton"
            onLoad={(e) => (e.currentTarget.style.background = "none")}
            onError={() => setFailed(true)}
          />
        )}
      </div>
      <div className="p-4">
        <p className="text-base font-semibold leading-6 text-foreground">
          {title}
        </p>
        <p className="text-[14px] leading-[23px] text-muted-foreground">
          {desc}
        </p>
      </div>
    </div>
  );
}

export default function ShowcaseGifs() {
  const cards: ShowcaseCardProps[] = [
    {
      gif: "/gifs/showcase-chat-code.gif",
      alt: m.showcase_chatcode_alt(),
      title: m.showcase_chatcode_title(),
      desc: m.showcase_chatcode_desc(),
      ready: false,
    },
    {
      gif: "/gifs/showcase-memory-buckets.gif",
      alt: m.showcase_memory_buckets_alt(),
      title: m.showcase_memory_buckets_title(),
      desc: m.showcase_memory_buckets_desc(),
      ready: false,
    },
    {
      gif: "/gifs/showcase-plan-remember.gif",
      alt: m.showcase_planremember_alt(),
      title: m.showcase_planremember_title(),
      desc: m.showcase_planremember_desc(),
      ready: false,
    },
    {
      gif: "/gifs/showcase-kanban-subagents.gif",
      alt: m.showcase_kanbansubagents_alt(),
      title: m.showcase_kanbansubagents_title(),
      desc: m.showcase_kanbansubagents_desc(),
      ready: false,
    },
  ];

  return (
    <section className="bg-background/50 py-[23px]">
      <div className="mx-auto flex max-w-[1024px] flex-col items-center gap-8 px-4 sm:px-6 lg:px-0">
        <h2 className="text-[28px] font-semibold leading-[36px] text-foreground">
          {m.showcase_title()}
        </h2>
        <div className="grid w-full grid-cols-1 gap-8 sm:grid-cols-2">
          {cards.map((card) => (
            <ShowcaseCard key={card.gif} {...card} />
          ))}
        </div>
      </div>
    </section>
  );
}
