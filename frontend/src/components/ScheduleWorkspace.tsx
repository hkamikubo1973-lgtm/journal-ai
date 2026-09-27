import { useEffect, useRef, useState, type FormEvent } from "react";
import { actOnEvent, createEvent, deleteEvent, editEvent, EventsApiError, fetchEvents, resolveScheduleOperation } from "../api/events";
import type { EventAction, EventInput, EventsResponse, ScheduleEvent } from "../types/events";

const cycleNames: Record<string, string> = { monthly: "月次", yearly: "年次" };
const typeNames: Record<string, string> = { tax: "税金", payment: "支払", card: "カード", other: "その他" };
const statusNames: Record<string, string> = { pending: "未処理", notified: "通知済", done: "完了", skip: "スキップ" };
const actionNames: Record<EventAction | "delete", string> = {
  complete: "完了", skip: "スキップ", stop: "停止", resume: "再開", delete: "削除",
};

export function daysLabel(event: ScheduleEvent): string {
  return event.days_remaining === 0 ? "本日" : `あと${event.days_remaining}日`;
}

export function ScheduleEventActions({ event, placement, pending, onAction, onEdit, onConfirm }: {
  event: ScheduleEvent;
  placement: "notification" | "list";
  pending: string | null;
  onAction: (event: ScheduleEvent, action: EventAction, key: string) => void;
  onEdit: (event: ScheduleEvent) => void;
  onConfirm: (event: ScheduleEvent, action: "complete" | "skip" | "delete") => void;
}) {
  const button = (action: EventAction | "delete", confirm = false) => {
    const key = `${placement}:${event.index}:${action}`;
    return <button key={action} type="button" className={action === "delete" ? "schedule-danger" : "schedule-secondary"}
      disabled={pending !== null} onClick={() => confirm ? onConfirm(event, action as "complete" | "skip" | "delete") : onAction(event, action as EventAction, key)}>
      {pending === key ? "処理中..." : actionNames[action]}
    </button>;
  };
  return <div className="schedule-actions">
    {placement === "list" && <button type="button" className="schedule-secondary" disabled={pending !== null} onClick={() => onEdit(event)}>編集</button>}
    {!event.stopped && placement === "notification" && <>{button("complete", true)}{button("skip", true)}</>}
    {event.stopped ? button("resume") : button("stop")}
    {placement === "list" && button("delete", true)}
  </div>;
}

export function SchedulePanel({ data, pending, onAction, onEdit, onConfirm, onNew }: {
  data: EventsResponse;
  pending: string | null;
  onAction: (event: ScheduleEvent, action: EventAction, key: string) => void;
  onEdit: (event: ScheduleEvent) => void;
  onConfirm: (event: ScheduleEvent, action: "complete" | "skip" | "delete") => void;
  onNew: () => void;
}) {
  const actions = { pending, onAction, onEdit, onConfirm };
  return <div className="schedule-content">
    <div className="schedule-heading"><div><h2>スケジュール</h2><p>通知対象 {data.notification_count}件</p></div>
      <button type="button" disabled={pending !== null} onClick={onNew}>＋ 新規登録</button></div>
    {data.notification_count > 0 && <section className="schedule-section" aria-label="通知対象">
      <h3>通知対象</h3><div className="schedule-notifications">{data.notification_events.map((event) =>
        <article key={event.index} className="schedule-notification">
          <div className="schedule-item-heading"><strong>{event.title}</strong><span className="schedule-due">{daysLabel(event)}</span></div>
          <p>次回予定日: {event.next_date} ・ {cycleNames[event.cycle] ?? event.cycle} ・ {typeNames[event.type] ?? event.type}</p>
          {event.memo && <p className="schedule-memo">{event.memo}</p>}
          <p>状態: {statusNames[event.effective_status] ?? event.effective_status}</p>
          <ScheduleEventActions event={event} placement="notification" {...actions} />
        </article>)}</div>
    </section>}
    <section className="schedule-section" aria-label="登録済みイベント"><h3>登録済みイベント</h3>
      {data.events.length === 0 ? <p className="schedule-empty">登録されているスケジュールはありません</p> :
        <div className="schedule-list">{data.events.map((event) =>
          <article key={event.index} className="schedule-list-item">
            <div className="schedule-item-heading"><strong>{event.title}</strong>{event.stopped && <span className="schedule-stopped">停止中</span>}</div>
            <p>次回予定日: {event.next_date} ・ {cycleNames[event.cycle] ?? event.cycle} ・ {typeNames[event.type] ?? event.type}</p>
            <p>状態: {statusNames[event.effective_status] ?? event.effective_status} ・ 通知: {event.notify_days}日前</p>
            {event.memo && <p className="schedule-memo">{event.memo}</p>}
            <ScheduleEventActions event={event} placement="list" {...actions} />
          </article>)}</div>}
    </section>
  </div>;
}

const emptyInput: EventInput = { title: "", cycle: "monthly", month: "", day: "1", notify_days: "0", type: "other", memo: "" };

export function ScheduleEditorDialog({ event, pending, onClose, onSave }: {
  event: ScheduleEvent | null;
  pending: boolean;
  onClose: () => void;
  onSave: (input: EventInput & { status?: string }) => void;
}) {
  const [input, setInput] = useState<EventInput>(() => event ? {
    title: event.title, cycle: event.cycle, month: event.month, day: event.day,
    notify_days: event.notify_days, type: event.type, memo: event.memo,
  } : emptyInput);
  const [status, setStatus] = useState(event?.status ?? "pending");
  const update = (field: keyof EventInput, value: string) => setInput((current) => ({ ...current, [field]: value }));
  const submit = (formEvent: FormEvent<HTMLFormElement>) => {
    formEvent.preventDefault();
    if (pending) return;
    onSave(event ? { ...input, month: input.cycle === "monthly" ? "" : input.month, status } :
      { ...input, month: input.cycle === "monthly" ? "" : input.month });
  };
  return <div className="schedule-modal-backdrop"><div role="dialog" aria-modal="true" aria-labelledby="schedule-editor-title" className="schedule-modal">
    <h2 id="schedule-editor-title">{event ? "イベントを編集" : "イベントを新規登録"}</h2>
    <form onSubmit={submit} className="schedule-form">
      <label>イベント名<input value={input.title} onChange={(e) => update("title", e.target.value)} required /></label>
      <div className="schedule-form-grid"><label>周期<select value={input.cycle} onChange={(e) => update("cycle", e.target.value)}>
        <option value="monthly">月次</option><option value="yearly">年次</option></select></label>
        {input.cycle === "yearly" && <label>月<input type="number" min="1" max="12" required value={input.month} onChange={(e) => update("month", e.target.value)} /></label>}
        <label>日<input type="number" min="1" max="31" required value={input.day} onChange={(e) => update("day", e.target.value)} /></label>
        <label>通知日前<input type="number" min="0" max="365" required value={input.notify_days} onChange={(e) => update("notify_days", e.target.value)} /></label>
        <label>種別<select value={input.type} onChange={(e) => update("type", e.target.value)}>
          <option value="tax">税金</option><option value="payment">支払</option><option value="card">カード</option><option value="other">その他</option></select></label>
        {event && <label>状態<select value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="pending">未処理</option><option value="notified">通知済</option><option value="done">完了</option><option value="skip">スキップ</option></select></label>}
      </div>
      <label>備考<textarea value={input.memo} onChange={(e) => update("memo", e.target.value)} /></label>
      <div className="schedule-modal-actions"><button type="button" className="schedule-secondary" disabled={pending} onClick={onClose}>キャンセル</button>
        <button type="submit" disabled={pending}>{pending ? "処理中..." : event ? "更新する" : "登録する"}</button></div>
    </form>
  </div></div>;
}

export function ScheduleConfirmDialog({ event, action, pending, onClose, onConfirm }: {
  event: ScheduleEvent;
  action: "complete" | "skip" | "delete";
  pending: boolean;
  onClose: () => void;
  onConfirm: () => void;
}) {
  return <div className="schedule-modal-backdrop"><div role="alertdialog" aria-modal="true" aria-labelledby="schedule-confirm-title" className="schedule-modal schedule-confirm">
    <h2 id="schedule-confirm-title">{actionNames[action]}の確認</h2>
    <p>{action === "complete" ? `「${event.title}」を完了にしますか？` :
      action === "skip" ? `「${event.title}」の今回の予定をスキップしますか？` : `「${event.title}」を削除しますか？`}</p>
    <div className="schedule-modal-actions"><button type="button" className="schedule-secondary" disabled={pending} onClick={onClose}>キャンセル</button>
      <button type="button" className={action === "delete" ? "schedule-danger" : ""} disabled={pending} onClick={onConfirm}>{pending ? "処理中..." : `${actionNames[action]}する`}</button></div>
  </div></div>;
}

export default function ScheduleWorkspace() {
  const [data, setData] = useState<EventsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [pending, setPending] = useState<string | null>(null);
  const pendingRef = useRef(false);
  const [editing, setEditing] = useState<{ event: ScheduleEvent | null } | null>(null);
  const [confirmation, setConfirmation] = useState<{ event: ScheduleEvent; action: "complete" | "skip" | "delete" } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    fetchEvents().then((result) => { if (active) setData(result); }).catch((failure: unknown) => {
      if (active) setError(failure instanceof EventsApiError ? failure.message : "イベント一覧を読み込めませんでした。");
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);

  const mutate = async (key: string, operation: () => Promise<unknown>, message: string) => {
    if (pendingRef.current) return;
    pendingRef.current = true;
    setPending(key); setError(null); setSuccess(null);
    try {
      const outcome = await resolveScheduleOperation(operation);
      setEditing(null); setConfirmation(null);
      try {
        setData(await fetchEvents());
        if (outcome === "conflict") setError("イベント一覧が更新されています。再読み込みしました。");
        else setSuccess(message);
      } catch {
        setError(outcome === "conflict" ? "イベント一覧が更新されています。再読み込みしてください。" :
          "イベント一覧を再読み込みできませんでした。画面を更新して確認してください。");
      }
    } catch (failure) {
      setError(failure instanceof EventsApiError ? failure.message : "イベントの処理に失敗しました。再読み込みして確認してください。");
    } finally {
      pendingRef.current = false; setPending(null);
    }
  };

  const onAction = (event: ScheduleEvent, action: EventAction, key: string) =>
    void mutate(key, () => actOnEvent(event, action), `イベントを${actionNames[action]}しました。`);
  const confirm = () => {
    if (!confirmation) return;
    const { event, action } = confirmation;
    void mutate(`confirm:${event.index}:${action}`, () => action === "delete" ? deleteEvent(event) : actOnEvent(event, action),
      `イベントを${actionNames[action]}しました。`);
  };
  const save = (input: EventInput & { status?: string }) => {
    if (!editing) return;
    const event = editing.event;
    void mutate("editor", () => event ? editEvent(event, { ...input, status: input.status ?? event.status }) : createEvent(input),
      event ? "イベントを更新しました。" : "イベントを登録しました。");
  };

  return <section className="schedule-workspace">
    {error && <p role="alert" className="schedule-message schedule-error">{error}</p>}
    {success && <p role="status" className="schedule-message schedule-success">{success}</p>}
    {loading ? <p className="schedule-loading">スケジュールを読み込み中...</p> : data ?
      <SchedulePanel data={data} pending={pending} onAction={onAction} onEdit={(event) => setEditing({ event })}
        onConfirm={(event, action) => setConfirmation({ event, action })} onNew={() => setEditing({ event: null })} /> :
      <button type="button" onClick={() => { setLoading(true); setError(null); fetchEvents().then(setData).catch(() => setError("イベント一覧を読み込めませんでした。")).finally(() => setLoading(false)); }}>再読み込み</button>}
    {editing && <ScheduleEditorDialog key={editing.event ? `edit:${editing.event.index}` : "new"} event={editing.event}
      pending={pending === "editor"} onClose={() => setEditing(null)} onSave={save} />}
    {confirmation && <ScheduleConfirmDialog event={confirmation.event} action={confirmation.action}
      pending={pending?.startsWith("confirm:") ?? false} onClose={() => setConfirmation(null)} onConfirm={confirm} />}
  </section>;
}
