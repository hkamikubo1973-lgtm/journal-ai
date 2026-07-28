from ai_client import generate_ai_search_explanation
from ai_search.prompt_builder import (
    build_ai_search_payload,
    build_ai_search_prompt,
)


def build_ai_search_context(
    keyword="",
    amount=None,
    department="",
    candidates=None,
    score_detail=None,
    visible_count=0,
    max_candidate_count=20,
    ocr_text=""
):
    """
    AIサーチへ渡すためのコンテキストを作成する。
    AIサーチは検索結果を説明するだけで、検索順位や仕訳内容は変更しない。
    """

    return {
        "keyword": keyword or "",
        "amount": amount,
        "department": department or "",
        "candidates": candidates or [],
        "score_detail": score_detail or [],
        "visible_count": visible_count or 0,
        "max_candidate_count": max_candidate_count or 20,
        "ocr_text": ocr_text or "",
    }


def build_fallback_ai_search_result():
    return {
        "summary": (
            "AIサーチ準備中です。現在は検索結果を変更せず、"
            "補足説明のみ表示します。"
        ),
        "reason": [
            "検索エンジンが提示した候補を前提にしています。",
            "AIは仕訳を決定せず、理由説明のみを行います。",
        ],
        "warning": [
            "候補の選択と登録は必ず人間が確認してください。",
        ],
    }


def run_ai_search(context):
    """
    AIサーチを実行する。
    Phase 2ではAIサーバー接続口を通し、失敗時はダミー説明を返す。
    """

    # TODO:
    # Phase 2 でAIサーバーへ接続する。
    # 通常時は4B、例外時のみ7Bを使う。
    # ただしAI結果は画面表示専用で、仕訳・検索順位・CSVには反映しない。
    try:
        prompt = build_ai_search_prompt(context or {})
        ai_result = generate_ai_search_explanation(
            prompt,
            model_role="normal"
        )
        if ai_result:
            return ai_result
    except Exception:
        pass

    return build_fallback_ai_search_result()
