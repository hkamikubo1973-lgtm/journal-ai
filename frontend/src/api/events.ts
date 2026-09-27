import type { EventAction, EventInput, EventSnapshot, EventsResponse, ScheduleEvent } from "../types/events";

export class EventsApiError extends Error {
  constructor(readonly status: number, message: string) {
    super(message);
    this.name = "EventsApiError";
  }
}

const messages: Record<number, string> = {
  409: "イベント一覧が更新されています。再読み込みしてください。",
  422: "入力内容を確認してください。",
  423: "イベント一覧はほかの処理で使用中です。しばらくしてから再試行してください。",
  503: "イベント一覧を読み込めません。内容を確認してください。",
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, init);
  } catch {
    throw new EventsApiError(0, "通信できません。接続を確認してください。");
  }
  if (!response.ok) {
    throw new EventsApiError(response.status, messages[response.status] ?? "イベントの処理に失敗しました。再読み込みして確認してください。");
  }
  return response.json() as Promise<T>;
}

const json = (value: unknown): RequestInit => ({
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(value),
});

export const fetchEvents = (): Promise<EventsResponse> => request("/api/events");
export const createEvent = (input: EventInput): Promise<{ event: EventSnapshot }> =>
  request("/api/events", { method: "POST", ...json(input) });
export const editEvent = (event: ScheduleEvent, input: EventInput & { status: string }): Promise<{ event: EventSnapshot }> =>
  request(`/api/events/${event.index}`, { method: "PUT", ...json({ ...input, expected_event: event.expected_event }) });
export const actOnEvent = (event: ScheduleEvent, action: EventAction): Promise<{ event: EventSnapshot }> =>
  request(`/api/events/${event.index}/${action}`, { method: "POST", ...json({ expected_event: event.expected_event }) });
export const deleteEvent = (event: ScheduleEvent): Promise<{ event: EventSnapshot }> =>
  request(`/api/events/${event.index}`, { method: "DELETE", ...json({ expected_event: event.expected_event }) });

export async function resolveScheduleOperation(operation: () => Promise<unknown>): Promise<"success" | "conflict"> {
  try {
    await operation();
    return "success";
  } catch (failure) {
    if (failure instanceof EventsApiError && failure.status === 409) return "conflict";
    throw failure;
  }
}

export type ScheduleFeedState = { data: EventsResponse | null; loading: boolean; error: string | null };

export function createScheduleFeed(
  publish: (patch: Partial<ScheduleFeedState>) => void,
  getEvents: () => Promise<EventsResponse> = fetchEvents,
): (forceNew?: boolean) => Promise<EventsResponse> {
  let inFlight: Promise<EventsResponse> | null = null;
  let requestId = 0;
  return (forceNew = false) => {
    if (!forceNew && inFlight) return inFlight;
    const currentId = ++requestId;
    publish({ loading: true, error: null });
    const request = getEvents().then((response) => {
      if (requestId === currentId) publish({ data: response, error: null });
      return response;
    }).catch((failure: unknown) => {
      if (requestId === currentId) publish({
        data: null,
        error: failure instanceof EventsApiError ? failure.message : "イベント一覧を読み込めませんでした。",
      });
      throw failure;
    }).finally(() => {
      if (requestId === currentId) {
        inFlight = null;
        publish({ loading: false });
      }
    });
    inFlight = request;
    return request;
  };
}
