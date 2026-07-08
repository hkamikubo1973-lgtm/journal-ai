import json


def build_ai_search_prompt(context):
    """
    AIサーチ用プロンプトを生成する。
    AIサーチは検索結果の補足説明だけを行い、検索順位や仕訳内容は変更しない。
    """

    prompt_payload = {
        "keyword": context.get("keyword", ""),
        "amount": context.get("amount", ""),
        "department": context.get("department", ""),
        "candidates": context.get("candidates", []) or [],
        "score_detail": context.get("score_detail", []) or [],
        "visible_count": context.get("visible_count", 0),
        "max_candidate_count": context.get("max_candidate_count", 20),
        "ocr_text": context.get("ocr_text", ""),
    }

    return "\n".join([
        "あなたは経理アシスタントです。",
        "検索結果を優先順位どおりに説明してください。",
        "AIの推測で新しい仕訳を作らないでください。",
        "仕訳を決定しないでください。",
        "検索順位を変更しないでください。",
        "人間が候補を選ぶための理由と注意点だけを説明してください。",
        "候補は最大20件あります。",
        "visible=true は画面表示中の候補です。",
        "visible=false はAIサーチ用に裏で参照している候補です。",
        "画面表示外の候補を参考にする場合は、その旨を明記してください。",
        "画面表示外の候補を勝手に採用しないでください。",
        "必要なら「表示件数を20件にすると確認できます」と案内してください。",
        "最終判断は人間が行う前提で説明してください。",
        "出力は summary / reason / warning のJSON形式にしてください。",
        "",
        "AIサーチ入力JSON:",
        json.dumps(
            prompt_payload,
            ensure_ascii=False,
            indent=2
        ),
    ])
