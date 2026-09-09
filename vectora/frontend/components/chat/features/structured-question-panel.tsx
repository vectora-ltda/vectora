import { useState } from "react";
import { Check, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { answerStructuredQuestion } from "@/lib/api/vectora-client";
import { m } from "@/lib/paraglide/messages";

interface StructuredQuestionPanelProps {
  pending: {
    questionId: string;
    threadId: string;
    prompt: string;
    options: string[];
    allowFreeText: boolean;
    expiresAt?: string;
  };
}

/** Cartão acessível para responder uma pergunta emitida pelo agente. */
export function StructuredQuestionPanel({
  pending,
}: StructuredQuestionPanelProps) {
  const [value, setValue] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (answer?: string, cancel = false) => {
    setError(null);
    try {
      await answerStructuredQuestion(
        pending.threadId,
        pending.questionId,
        answer,
        cancel,
      );
      setSubmitted(true);
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : m.chat_structured_question_error(),
      );
    }
  };

  if (submitted) {
    return (
      <div className="mt-3 rounded-lg border border-primary/30 bg-primary/5 p-3 text-sm text-muted-foreground">
        {m.chat_structured_question_answered()}
      </div>
    );
  }

  return (
    <section
      className="mt-3 rounded-lg border border-primary/40 bg-background p-4"
      aria-label={m.chat_structured_question_label()}
    >
      <p className="mb-3 text-sm font-medium">{pending.prompt}</p>
      <div className="flex flex-wrap gap-2">
        {pending.options.map((option) => (
          <Button
            key={option}
            type="button"
            variant="outline"
            size="sm"
            onClick={() => submit(option)}
          >
            <Check className="mr-1 h-4 w-4" />
            {option}
          </Button>
        ))}
      </div>
      {pending.allowFreeText && (
        <div className="mt-3 space-y-2">
          <Textarea
            value={value}
            onChange={(event) => setValue(event.target.value)}
            placeholder={m.chat_structured_question_free_text_placeholder()}
            aria-label={m.chat_structured_question_free_text_label()}
          />
          <Button
            type="button"
            size="sm"
            disabled={!value.trim()}
            onClick={() => submit(value.trim())}
          >
            {m.chat_structured_question_send()}
          </Button>
        </div>
      )}
      <Button
        type="button"
        variant="ghost"
        size="sm"
        className="mt-2"
        onClick={() => submit(undefined, true)}
      >
        <X className="mr-1 h-4 w-4" />
        {m.chat_structured_question_cancel()}
      </Button>
      {error && (
        <p role="alert" className="mt-2 text-sm text-destructive">
          {error}
        </p>
      )}
      {pending.expiresAt && (
        <p className="mt-2 text-xs text-muted-foreground">
          {m.chat_structured_question_expiry()}
        </p>
      )}
    </section>
  );
}
