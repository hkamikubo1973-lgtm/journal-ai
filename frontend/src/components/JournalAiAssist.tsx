import { useEffect, useRef, useState } from "react";
import { JournalAiAssistController, initialJournalAiAssistView, type JournalAiAssistViewState } from "../journalAiAssistController";
import type { JournalSearchRequest } from "../types/journal";

type ViewProps = {
  available: boolean;
  view: JournalAiAssistViewState;
  onOpen: () => void;
  onClose: () => void;
  onRetryPoll: () => void;
};

export function JournalAiAssistView({ available, view, onOpen, onClose, onRetryPoll }: ViewProps) {
  const error = view.error || (view.state === "FAILED" ? "AI補助の処理に失敗しました。" : null);
  const processing = !error && (view.submitting || view.state === "QUEUED" || view.state === "RUNNING");
  const idle = !view.submitting && view.state === null && !error;
  const status = error ? "失敗" : view.submitting || view.state === "QUEUED" ? "準備中"
    : view.state === "RUNNING" ? "処理中" : view.state === "COMPLETED" ? "完了" : "未実行";
  const statusStyle = error ? "error" : processing ? "processing" : view.state === "COMPLETED" ? "complete" : "idle";
  return <>
    <button type="button" className={`journal-ai-trigger${view.open ? " is-open" : ""}`} data-candidate-tab="" aria-controls="journal-ai-panel"
      aria-expanded={view.open} disabled={!available || view.open} onClick={onOpen}>AI補助</button>
    {view.open && <aside id="journal-ai-panel" className="journal-ai-panel" aria-label="AI補助" aria-busy={processing}>
      <div className="journal-ai-panel-heading">
        <div className="journal-ai-panel-title"><h2>AI補助</h2><span className={`journal-ai-state journal-ai-state--${statusStyle}`}>{status}</span></div>
        <button type="button" className="journal-ai-close" aria-label="AI補助を閉じる" onClick={onClose}>閉じる</button>
      </div>
      <p className="journal-ai-note">AIは検索候補の整理・説明を補助します。最終判断は人が行ってください。</p>
      <div className="journal-ai-panel-body" aria-live="polite">
        {idle && <p className="journal-ai-idle">検索候補についてAIの説明を表示できます。</p>}
        {processing && (view.submitting || view.state === "QUEUED") ? <p className="journal-ai-loading">AI補助を準備しています…</p> : null}
        {processing && !view.submitting && view.state === "RUNNING" ? <p className="journal-ai-loading">AIが検索候補を整理しています…</p> : null}
        {error && <p className="journal-ai-error" role="alert">{error}</p>}
        {view.pollRetryAvailable && view.jobId && <button type="button" className="journal-ai-retry" onClick={onRetryPoll}>同じJobを再確認</button>}
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
    onClose={() => controller.current?.close()}
    onRetryPoll={() => controller.current?.retryPoll()} />;
}
