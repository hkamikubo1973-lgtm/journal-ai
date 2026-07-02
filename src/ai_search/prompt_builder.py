import json


def build_ai_search_prompt(context):
    """
    AIサーチ用プロンプトを生成する。
    現時点ではAIサーバー未接続でも、将来の接続に備えて分離しておく。
    """

    prompt_payload = {
        "keyword": context.get("keyword", ""),
        "amount": context.get("amount", ""),
        "department": context.get("department", ""),
        "candidates": context.get("candidates", []) or [],
        "score_detail": context.get("score_detail", []) or [],
        "ocr_text": context.get("ocr_text", ""),
    }

    return "\n".join([
        "あなたは経理アシスタントです。",
        "検索結果を最優先に説明してください。",
        "AIの推測で新しい仕訳を作らないでください。",
        "仕訳を決定しないでください。",
        "検索順位を変更しないでください。",
        "人間が候補を選ぶための理由と注意点だけを説明してください。",
        "出力は summary / reason / warning のJSON形式にしてください。",
        "",
        "AIサーチ入力JSON:",
        json.dumps(
            prompt_payload,
            ensure_ascii=False,
            indent=2
        ),
    ])
