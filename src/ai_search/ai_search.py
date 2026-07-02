def build_ai_search_context(
    keyword="",
    amount=None,
    department="",
    candidates=None,
    score_detail=None,
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
        "ocr_text": ocr_text or "",
    }


def run_ai_search(context):
    """
    AIサーチを実行する。
    Phase 0〜1ではAIサーバー未接続のため、ダミー説明を返す。
    将来ここをAIクライアント呼び出しに差し替える。
    """

    # TODO:
    # Phase 2 でAIサーバーへ接続する。
    # 通常時は4B、例外時のみ7Bを使う。
    # ただしAI結果は画面表示専用で、仕訳・検索順位・CSVには反映しない。

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
