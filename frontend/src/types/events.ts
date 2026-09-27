export type EventSnapshot = {
  month: string;
  day: string;
  title: string;
  memo: string;
  notify_days: string;
  cycle: string;
  status: string;
  type: string;
  stop: string;
  last_executed: string;
};

export type ScheduleEvent = EventSnapshot & {
  index: number;
  expected_event: EventSnapshot;
  effective_status: string;
  stopped: boolean;
  next_date: string;
  days_remaining: number;
  notification_target: boolean;
};

export type EventsResponse = {
  events: ScheduleEvent[];
  notification_events: ScheduleEvent[];
  notification_count: number;
};

export type EventInput = Pick<EventSnapshot, "title" | "cycle" | "month" | "day" | "notify_days" | "type" | "memo">;
export type EventAction = "complete" | "skip" | "stop" | "resume";
