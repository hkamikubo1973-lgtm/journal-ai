import { getJournalAiAssist, submitJournalAiAssist, JournalAiAssistApiError } from "./api/journalAiAssist";
import type { JournalAiAssistResponse, JournalAiAssistState, JournalSearchRequest } from "./types/journal";

export type JournalAiAssistViewState = {
  open: boolean;
  submitting: boolean;
  jobId: string | null;
  state: JournalAiAssistState | null;
  content: string | null;
  error: string | null;
  pollRetryAvailable: boolean;
};

export const initialJournalAiAssistView: JournalAiAssistViewState = {
  open: false, submitting: false, jobId: null, state: null, content: null, error: null,
  pollRetryAvailable: false,
};

export function matchesJournalAiSearch(current: JournalSearchRequest, searched: JournalSearchRequest | null): boolean {
  return searched !== null && current.keyword === searched.keyword
    && current.department === searched.department && current.amount === searched.amount
    && current.limit === searched.limit;
}

type Timer = { set(callback: () => void, delay: number): number; clear(id: number): void };
const browserTimer: Timer = {
  set: (callback, delay) => window.setTimeout(callback, delay),
  clear: (id) => window.clearTimeout(id),
};

const safeError = (error: unknown): string => error instanceof JournalAiAssistApiError
  ? error.message : "AI補助の結果を取得できませんでした。";
const isPollTimeout = (error: unknown): boolean => error instanceof JournalAiAssistApiError
  && error.code === "AI_JOB_POLL_TIMEOUT";

export class JournalAiAssistController {
  private view: JournalAiAssistViewState = { ...initialJournalAiAssistView };
  private generation = 0;
  private pollGeneration = 0;
  private timerId: number | null = null;
  private disposed = false;

  constructor(
    private readonly onChange: (view: JournalAiAssistViewState) => void,
    private readonly submit: (request: JournalSearchRequest) => Promise<JournalAiAssistResponse> = submitJournalAiAssist,
    private readonly get: (jobId: string) => Promise<JournalAiAssistResponse> = getJournalAiAssist,
    private readonly timer: Timer = browserTimer,
  ) {}

  snapshot(): JournalAiAssistViewState { return { ...this.view }; }

  private update(patch: Partial<JournalAiAssistViewState>): void {
    if (this.disposed) return;
    this.view = { ...this.view, ...patch };
    this.onChange(this.snapshot());
  }

  private stopPolling(): void {
    this.pollGeneration += 1;
    if (this.timerId !== null) this.timer.clear(this.timerId);
    this.timerId = null;
  }

  private schedulePoll(delay: number): void {
    if (!this.view.open || !this.view.jobId || this.view.content || this.view.error || this.disposed) return;
    this.stopPolling();
    const pollGeneration = this.pollGeneration;
    const jobId = this.view.jobId;
    this.timerId = this.timer.set(() => {
      this.timerId = null;
      void this.get(jobId).then((response) => {
        if (this.disposed || pollGeneration !== this.pollGeneration || this.view.jobId !== jobId || !this.view.open) return;
        if (response.job_id !== jobId) throw new Error("AI補助の結果を取得できませんでした。");
        if (response.state === "FAILED") {
          this.update({ state: "FAILED", error: "AI補助の処理に失敗しました。", pollRetryAvailable: false });
        } else if (response.state === "COMPLETED") {
          if (!response.content?.trim()) throw new Error("AI補助の結果を取得できませんでした。");
          this.update({ state: "COMPLETED", content: response.content, error: null, pollRetryAvailable: false });
        } else {
          this.update({ state: response.state, error: null, pollRetryAvailable: false });
          this.schedulePoll(1500);
        }
      }).catch((error: unknown) => {
        if (this.disposed || pollGeneration !== this.pollGeneration || this.view.jobId !== jobId || !this.view.open) return;
        this.update({ error: safeError(error), pollRetryAvailable: isPollTimeout(error) });
      });
    }, delay);
  }

  async open(request: JournalSearchRequest | null): Promise<void> {
    if (this.disposed || !request) return;
    this.update({ open: true });
    if (this.view.jobId) {
      if (!this.view.content && !this.view.error) this.schedulePoll(0);
      return;
    }
    if (this.view.submitting || this.view.error) return;
    this.update({ submitting: true });
    const generation = ++this.generation;
    try {
      const response = await this.submit(request);
      if (this.disposed || generation !== this.generation) return;
      this.update({ jobId: response.job_id, state: response.state, pollRetryAvailable: false });
      if (response.state === "FAILED") this.update({ error: "AI補助の処理に失敗しました。", pollRetryAvailable: false });
      else this.schedulePoll(1500);
    } catch (error) {
      if (!this.disposed && generation === this.generation) {
        this.update({ error: safeError(error), pollRetryAvailable: false });
      }
    } finally {
      if (!this.disposed && generation === this.generation) this.update({ submitting: false });
    }
  }

  close(): void {
    this.stopPolling();
    this.update({ open: false });
  }

  retryPoll(): void {
    if (this.disposed || !this.view.jobId || !this.view.pollRetryAvailable) return;
    this.update({ error: null, pollRetryAvailable: false });
    this.schedulePoll(0);
  }

  dispose(): void {
    this.generation += 1;
    this.stopPolling();
    this.disposed = true;
  }
}
