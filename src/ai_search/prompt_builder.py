def build_ai_search_prompt(context):
    """
    AIサーチ用プロンプトを生成する。
    現時点ではAIサーバー未接続だが、将来の接続に備えて分離しておく。
    """

    return "\n".join([
        "あなたは経理アシスタントです。",
        "検索結果を最優先に説明してください。",
        "AIの推測で新しい仕訳を作らないでください。",
        "仕訳を決定しないでください。",
        "検索順位を変更しないでください。",
        "人間が候補を選ぶための理由と注意点だけを説明してください。",
        "",
        f"検索キーワード: {context.get('keyword', '')}",
        f"金額: {context.get('amount', '')}",
        f"部門: {context.get('department', '')}",
        f"候補数: {len(context.get('candidates', []) or [])}",
    ])
