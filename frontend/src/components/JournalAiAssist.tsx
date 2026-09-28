import { useEffect, useRef, useState } from "react";
import { JournalAiAssistController, initialJournalAiAssistView, type JournalAiAssistViewState } from "../journalAiAssistController";
import type { JournalSearchRequest } from "../types/journal";

type ViewProps = {
  available: boolean;
  view: JournalAiAssistViewState;
  onOpen: () => void;
  onClose: () => void;
};

export function JournalAiAssistView({ available, view, onOpen, onClose }: ViewProps) {
  const processing = !view.error && (view.submitting || view.state === "QUEUED" || view.state === "RUNNING");
  return <>
    <button type="button" className="journal-ai-trigger" data-candidate-tab="" aria-controls="journal-ai-panel"
      aria-expanded={view.open} disabled={!available || (view.open && processing)} onClick={onOpen}>AI補助</button>
    {view.open && <aside id="journal-ai-panel" className="journal-ai-panel" aria-label="AI補助" aria-busy={processing}>
      <div className="journal-ai-panel-heading">
        <h2>AI補助</h2>
        <button type="button" className="journal-ai-close" aria-label="AI補助を閉じる" onClick={onClose}>閉じる</button>
      </div>
      <p className="journal-ai-note">AIは検索候補の整理・説明を補助します。最終判断は人が行ってください。</p>
      <div className="journal-ai-panel-body" aria-live="polite">
        {processing && (view.submitting || view.state === "QUEUED") ? <p className="journal-ai-loading">AI補助を準備しています…</p> : null}
        {processing && !view.submitting && view.state === "RUNNING" ? <p className="journal-ai-loading">AIが候補を整理しています…</p> : null}
        {view.error && <p className="journal-ai-error" role="alert">{view.error}</p>}
        {view.state === "COMPLETED" && view.content && <p className="journal-ai-content">{view.content}</p>}
      </div>
    </aside>}
  </>;
}

export default function JournalAiAssist({ request, candidateCount }: {
  request: JournalSearchRequest | null;
  candidateCount: number;
}) {
  const [view, setView] = useState<JournalAiAssistViewState>(initialJournalAiAssistView);
  const controller = useRef<JournalAiAssistController | null>(null);
  useEffect(() => {
    const current = new JournalAiAssistController(setView);
    controller.current = current;
    return () => {
      current.dispose();
      if (controller.current === current) controller.current = null;
    };
  }, []);
  return <JournalAiAssistView available={Boolean(request && candidateCount > 0)} view={view}
    onOpen={() => { void controller.current?.open(request); }}
    onClose={() => controller.current?.close()} />;
}
