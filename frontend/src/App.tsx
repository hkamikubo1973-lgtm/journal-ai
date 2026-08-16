import { useState, type FormEvent } from "react";
import { searchJournals } from "./api/journal";
import type {
  JournalCandidate,
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

function displayValue(value: unknown): string {
  if (value === null || value === undefined || value === "") {
    return "-";
  }
  if (typeof value === "object") {
    return JSON.stringify(value);
  }
  return String(value);
}

function CandidateCard({ candidate }: { candidate: JournalCandidate }) {
  return (
    <article className="candidate-card">
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
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLoading(true);
    setError(null);

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
              />
            ))}
          </div>
          <details className="json-details">
            <summary>レスポンスJSONを表示</summary>
            <pre>{JSON.stringify(result, null, 2)}</pre>
          </details>
        </section>
      )}
    </main>
  );
}
