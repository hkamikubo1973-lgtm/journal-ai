import os

try:
    import requests
except ImportError:
    requests = None


AI_SERVER_URL = os.getenv("JOURNAL_AI_SERVER_URL", "").strip()


def normalize_ai_search_response(data):
    """
    AIサーチ結果を画面表示用dictへ正規化する。
    不正な場合は None を返す。
    """

    if not isinstance(data, dict):
        return None

    summary = data.get("summary", "")
    reason = data.get("reason", [])
    warning = data.get("warning", [])

    if summary is None:
        summary = ""

    if isinstance(reason, str):
        reason = [reason]
    elif not isinstance(reason, list):
        return None

    if isinstance(warning, str):
        warning = [warning]
    elif not isinstance(warning, list):
        return None

    return {
        "summary": str(summary),
        "reason": [
            str(item)
            for item in reason
            if item not in (None, "")
        ],
        "warning": [
            str(item)
            for item in warning
            if item not in (None, "")
        ],
    }


def generate_ai_search_explanation(
    prompt,
    model_role="normal",
    timeout=30
):
    """
    AIサーチ用の説明生成をAIサーバーへ依頼する。

    Phase 2では接続口のみを用意する。
    AIサーバーURLが未設定、または接続失敗した場合は None を返す。

    戻り値:
        dict または None

    dict形式:
        {
            "summary": "...",
            "reason": [...],
            "warning": [...]
        }
    """

    # normal: 通常時の説明生成。将来4B想定。
    # advanced: 例外時のみ使用。将来7B想定。
    # AI結果は画面表示専用で、仕訳・検索順位・CSVには反映しない。
    if model_role not in {"normal", "advanced"}:
        model_role = "normal"

    if not AI_SERVER_URL or requests is None:
        return None

    try:
        response = requests.post(
            f"{AI_SERVER_URL.rstrip('/')}/ai-search",
            json={
                "prompt": prompt or "",
                "model_role": model_role,
            },
            timeout=timeout,
        )
        response.raise_for_status()
        return normalize_ai_search_response(response.json())
    except Exception:
        return None
