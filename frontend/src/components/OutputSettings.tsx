import { useEffect, useState, type FormEvent } from "react";
import { getOutputSettings, saveOutputSettings } from "../api/outputSettings";

export function OutputSettingsForm({ value, onChange, onSave, busy, error, message }: {
  value: string;
  onChange: (value: string) => void;
  onSave: () => void;
  busy: boolean;
  error: string;
  message: string;
}) {
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    onSave();
  }
  return <form className="output-settings" onSubmit={submit}>
    <h3>出力保存先フォルダ</h3>
    <label htmlFor="csv-export-dir">保存先フォルダ</label>
    <input id="csv-export-dir" type="text" value={value} onChange={event => onChange(event.target.value)}
      placeholder={String.raw`C:\仕訳出力 または \\server\share\仕訳システム`} autoComplete="off" spellCheck={false} />
    <p>「保存先へ保存」で使用するフォルダです。ブラウザのダウンロード先とは別です。</p>
    <button type="submit" disabled={busy || !value.trim()}>{busy ? "設定を保存中…" : "設定を保存"}</button>
    {error && <p role="alert" className="error-message">{error}</p>}
    {message && <p role="status" className="status-message">{message}</p>}
  </form>;
}

export default function OutputSettings() {
  const [value, setValue] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  useEffect(() => {
    let active = true;
    void getOutputSettings().then(settings => {
      if (active) setValue(settings.csv_export_dir);
    }).catch(() => {
      if (active) setError("現在の保存先設定を取得できませんでした。画面を再読み込みしてください。");
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);

  async function save() {
    if (loading || saving || !value.trim()) return;
    setSaving(true);
    setError("");
    setMessage("");
    try {
      const settings = await saveOutputSettings(value);
      setValue(settings.csv_export_dir);
      setMessage("出力保存先フォルダを保存しました。");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "保存先フォルダを保存できませんでした。");
    } finally {
      setSaving(false);
    }
  }

  return <OutputSettingsForm value={value} onChange={next => { setValue(next); setError(""); setMessage(""); }}
    onSave={() => void save()} busy={loading || saving} error={error} message={message} />;
}
