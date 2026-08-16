import { useState, type FormEvent } from "react";
import { searchJournals } from "./api/journal";
import type {
  JournalCandidate,
  JournalEditForm,
  JournalSearchRequest,
  JournalSearchResponse,
} from "./types/journal";

const blockRowFields = [
  "date",
  "debit",
  "credit",
  "debit_sub",
  "credit_sub",
  "debit_amount",
  "credit_amount",
  "summary",
] as const;

const editFormFields: Array<{
  key: keyof JournalEditForm;
  label: string;
  wide?: boolean;
}> = [
  { key: "voucherDate", label: "伝票日付" },
  { key: "debitAccountCode", label: "借方科目コード" },
  { key: "debitAccountName", label: "借方科目名" },
  { key: "debitSubCode", label: "借方補助コード" },
  { key: "debitSubName", label: "借方補助名" },
  { key: "debitDeptCode", label: "借方部門コード" },
  { key: "debitDeptName", label: "借方部門名" },
  { key: "debitAmount", label: "借方金額" },
  { key: "creditAccountCode", label: "貸方科目コード" },
  { key: "creditAccountName", label: "貸方科目名" },
  { key: "creditSubCode", label: "貸方補助コード" },
  { key: "creditSubName", label: "貸方補助名" },
  { key: "creditDeptCode", label: "貸方部門コード" },
  { key: "creditDeptName", label: "貸方部門名" },
  { key: "creditAmount", label: "貸方金額" },
  { key: "summary", label: "摘要", wide: true },
  { key: "voucherSummary", label: "伝票摘要", wide: true },
  { key: "voucherNo", label: "証番号" },
];

function getString(row: Record<string, unknown>, key: string): string {
  const value = row[key];
  if (value === null || value === undefined) {
    return "";
  }
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}

function buildEditFormFromRow(
  row: Record<string, unknown>,
): JournalEditForm {
  return {
    voucherDate: getString(row, "伝票日付"),
    debitAccountCode: getString(row, "借方科目"),
    debitAccountName: getString(row, "借方科目名"),
    debitSubCode: getString(row, "借方補助"),
    debitSubName: getString(row, "借方補助科目名"),
    debitDeptCode: getString(row, "借方部門"),
    debitDeptName: getString(row, "借方部門名"),
    debitAmount: getString(row, "借方金額"),
    creditAccountCode: getString(row, "貸方科目"),
    creditAccountName: getString(row, "貸方科目名"),
    creditSubCode: getString(row, "貸方補助"),
    creditSubName: getString(row, "貸方補助科目名"),
    creditDeptCode: getString(row, "貸方部門"),
    creditDeptName: getString(row, "貸方部門名"),
    creditAmount: getString(row, "貸方金額"),
    summary: getString(row, "摘要"),
    voucherSummary: getString(row, "伝票摘要"),
    voucherNo: getString(row, "証番号"),
  };
}

function displayValue(value: unknown): string {
  if (value === null || value === undefined || value === "") {
    return "-";
  }
  if (typeof value === "object") {
    return JSON.stringify(value);
  }
  return String(value);
}

function CandidateCard({
  candidate,
  selected,
  onSelect,
}: {
  candidate: JournalCandidate;
  selected: boolean;
  onSelect: (candidate: JournalCandidate) => void;
}) {
  return (
    <article
      className={`candidate-card${selected ? " candidate-card-selected" : ""}`}
      aria-current={selected ? "true" : undefined}
    >
      <h2>
        候補{candidate.rank} <span>/ Score {candidate.score}</span>
      </h2>
      <dl className="candidate-summary">
        <div>
          <dt>pattern_key</dt>
          <dd>{candidate.pattern_key.join(" / ") || "-"}</dd>
        </div>
        <div>
          <dt>pattern_rank</dt>
          <dd>{candidate.pattern_rank ?? "-"}</dd>
        </div>
        <div>
          <dt>source_rows</dt>
          <dd>{candidate.source_rows.length}</dd>
        </div>
        <div>
          <dt>editable_rows</dt>
          <dd>{candidate.editable_rows.length}</dd>
        </div>
        <div>
          <dt>block_rows</dt>
          <dd>{candidate.block_rows.length}</dd>
        </div>
        <div>
          <dt>資金複合</dt>
          <dd>{candidate.has_fukugo ? "あり" : "なし"}</dd>
        </div>
        <div>
          <dt>諸口</dt>
          <dd>{candidate.has_sundry ? "あり" : "なし"}</dd>
        </div>
      </dl>

      <button
        className="candidate-select-button"
        type="button"
        onClick={() => onSelect(candidate)}
      >
        {selected ? "編集フォームへ反映済み" : "この候補を編集フォームへ反映"}
      </button>

      <section className="candidate-section">
        <h3>検索理由</h3>
        {candidate.search_reason.length > 0 ? (
          <ul>
            {candidate.search_reason.slice(0, 3).map((reason, index) => (
              <li key={`${reason}-${index}`}>{reason}</li>
            ))}
          </ul>
        ) : (
          <p className="muted">検索理由はありません。</p>
        )}
      </section>

      {candidate.block_rows.length > 0 && (
        <section className="candidate-section">
          <h3>block_rows（先頭3件）</h3>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  {blockRowFields.map((field) => (
                    <th key={field}>{field}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {candidate.block_rows.slice(0, 3).map((row, index) => (
                  <tr key={index}>
                    {blockRowFields.map((field) => (
                      <td key={field}>{displayValue(row[field])}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </article>
  );
}

export default function App() {
  const [keyword, setKeyword] = useState("りそな銀行");
  const [department, setDepartment] = useState("");
  const [amount, setAmount] = useState("");
  const [limit, setLimit] = useState<5 | 10 | 20>(5);
  const [result, setResult] = useState<JournalSearchResponse | null>(null);
  const [selectedCandidate, setSelectedCandidate] =
    useState<JournalCandidate | null>(null);
  const [editForm, setEditForm] = useState<JournalEditForm | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLoading(true);
    setError(null);
    setSelectedCandidate(null);
    setEditForm(null);

    const request: JournalSearchRequest = {
      keyword,
      department: department.trim() || null,
      amount: amount === "" ? null : Number(amount),
      limit,
    };

    try {
      setResult(await searchJournals(request));
    } catch (caughtError) {
      setResult(null);
      setError(
        caughtError instanceof Error
          ? caughtError.message
          : "検索中に不明なエラーが発生しました。",
      );
    } finally {
      setLoading(false);
    }
  }

  function handleCandidateSelect(candidate: JournalCandidate) {
    setSelectedCandidate(candidate);
    const firstEditableRow = candidate.editable_rows[0];
    setEditForm(
      firstEditableRow ? buildEditFormFromRow(firstEditableRow) : null,
    );
  }

  function updateEditForm(key: keyof JournalEditForm, value: string) {
    setEditForm((current) =>
      current
        ? {
            ...current,
            [key]: value,
          }
        : current,
    );
  }

  const selectedCandidateIsComplex = Boolean(
    selectedCandidate &&
      (selectedCandidate.has_fukugo ||
        selectedCandidate.has_sundry ||
        selectedCandidate.contains_fukugo_or_sundry ||
        selectedCandidate.show_block_rows ||
        selectedCandidate.is_complex),
  );

  return (
    <main className="page-shell">
      <header>
        <p className="eyebrow">API connection check</p>
        <h1>journal-ai 正式UI Phase 2 検索API確認</h1>
        <p className="intro">
          通常仕訳検索APIの候補DTOをReactから確認するための最小画面です。
        </p>
      </header>

      <form className="search-form" onSubmit={handleSubmit}>
        <label>
          キーワード
          <input
            value={keyword}
            onChange={(event) => setKeyword(event.target.value)}
            required
          />
        </label>
        <label>
          部門
          <input
            value={department}
            onChange={(event) => setDepartment(event.target.value)}
            placeholder="空欄可"
          />
        </label>
        <label>
          金額
          <input
            type="number"
            value={amount}
            onChange={(event) => setAmount(event.target.value)}
            placeholder="空欄可"
          />
        </label>
        <label>
          表示件数
          <select
            value={limit}
            onChange={(event) =>
              setLimit(Number(event.target.value) as 5 | 10 | 20)
            }
          >
            <option value={5}>5</option>
            <option value={10}>10</option>
            <option value={20}>20</option>
          </select>
        </label>
        <button type="submit" disabled={loading}>
          {loading ? "検索中…" : "検索"}
        </button>
      </form>

      {error && <p className="error-message">{error}</p>}

      {result && (
        <section className="results" aria-live="polite">
          <h2 className="result-count">検索結果: {result.count}件</h2>
          <div className="candidate-list">
            {result.candidates.map((candidate) => (
              <CandidateCard
                key={`${candidate.rank}-${candidate.pattern_key.join("-")}`}
                candidate={candidate}
                selected={selectedCandidate === candidate}
                onSelect={handleCandidateSelect}
              />
            ))}
          </div>
          <details className="json-details">
            <summary>レスポンスJSONを表示</summary>
            <pre>{JSON.stringify(result, null, 2)}</pre>
          </details>
        </section>
      )}

      <section className="edit-panel" aria-live="polite">
        <div className="edit-panel-heading">
          <div>
            <p className="status-badge">確認用・未登録</p>
            <h2>選択中の仕訳編集フォーム（確認用・未登録）</h2>
          </div>
          <button type="button" disabled>
            登録APIは未実装です
          </button>
        </div>

        {!selectedCandidate && (
          <p className="empty-edit-message">
            候補を選択すると、editable_rows[0] の内容をここに表示します。
          </p>
        )}

        {selectedCandidate && (
          <>
            <p className="editing-candidate">
              候補{selectedCandidate.rank}を編集中
            </p>

            {selectedCandidate.editable_rows.length === 0 && (
              <p className="notice notice-error">
                この候補には編集用行がありません。
              </p>
            )}

            {selectedCandidate.editable_rows.length > 1 && (
              <p className="notice notice-warning">
                この候補は複数行の編集候補です。今回は先頭行のみ表示しています。
              </p>
            )}

            {selectedCandidateIsComplex && (
              <p className="notice notice-warning">
                この候補は資金複合または諸口を含む可能性があります。block_rows
                を確認し、登録時は実際の相手科目へ修正してください。
              </p>
            )}

            {editForm && (
              <>
                <form
                  className="edit-form"
                  onSubmit={(event) => event.preventDefault()}
                >
                  {editFormFields.map((field) => (
                    <label
                      className={field.wide ? "edit-field-wide" : undefined}
                      key={field.key}
                    >
                      {field.label}
                      {field.wide ? (
                        <textarea
                          value={editForm[field.key]}
                          onChange={(event) =>
                            updateEditForm(field.key, event.target.value)
                          }
                          rows={3}
                        />
                      ) : (
                        <input
                          value={editForm[field.key]}
                          onChange={(event) =>
                            updateEditForm(field.key, event.target.value)
                          }
                        />
                      )}
                    </label>
                  ))}
                </form>

                <details className="json-details edit-json-details">
                  <summary>編集フォームstateを表示</summary>
                  <pre>{JSON.stringify(editForm, null, 2)}</pre>
                </details>
              </>
            )}
          </>
        )}
      </section>
    </main>
  );
}
