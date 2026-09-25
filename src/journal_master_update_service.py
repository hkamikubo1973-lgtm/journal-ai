"""Additive master maintenance. All validation precedes any file replacement."""

import csv
import io
import threading
from pathlib import Path

from receivable_persistence_service import atomic_write_bytes


MASTER_LOCK = threading.RLock()
CATEGORIES = ("資産", "負債", "純資産", "収益", "費用")
SCHEMAS = {
    "account": ("account_master.csv", ["code", "name", "category"]),
    "department": ("department_master.csv", ["code", "name"]),
    "sub": ("sub_master.csv", ["code", "name"]),
    "relation": ("sub_account_relations.csv", ["account_code", "sub_code", "sub_name"]),
    "payment": ("payment_accounts.csv", ["科目"]),
}


class MasterUpdateError(ValueError):
    pass


def _text(value):
    return str(value or "").strip()


def _load(root, kind):
    filename, required = SCHEMAS[kind]
    path = root / filename
    raw = path.read_bytes() if path.exists() else b""
    if not raw.strip(b"\xef\xbb\xbf\r\n\t "):
        return required[:], []
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig"), newline=""))
    fields = reader.fieldnames or []
    # category may be absent in older account files; retain all other columns.
    mandatory = [f for f in required if f != "category"]
    if not set(mandatory).issubset(fields):
        raise MasterUpdateError("マスターの列構造を確認してください。")
    rows = list(reader)
    if any(None in r or any(v is None for v in r.values()) for r in rows):
        raise MasterUpdateError("マスターの行構造を確認してください。")
    return fields, rows


def _bytes(fields, rows):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8-sig")


def _save_batch(updates):
    """Single-file atomic writes, with exact rollback on ordinary write failure.

    Multiple filesystem replacements are not crash-atomic. In-process callers
    are serialized by MASTER_LOCK; external CSV writers must not run concurrently.
    """
    before = {path: path.read_bytes() if path.exists() else None for path in updates}
    attempted = []
    try:
        for path, content in updates.items():
            attempted.append(path)  # helper can fail after replacing the file
            atomic_write_bytes(path, content)
    except Exception:
        for path in reversed(attempted):
            if before[path] is None:
                path.unlink(missing_ok=True)
            elif not path.exists() or path.read_bytes() != before[path]:
                atomic_write_bytes(path, before[path])
        raise


def _found(root, kind):
    with (root / "transactions.csv").open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        required = []
        for side in ("借方", "貸方"):
            required += ([side + "科目", side + "補助", side + "補助科目名"] if kind == "sub"
                         else [side + ("科目" if kind == "account" else "部門"),
                               side + ("科目名" if kind == "account" else "部門名")])
        if not set(required).issubset(reader.fieldnames or []):
            raise MasterUpdateError("検索DBの列構造を確認してください。")
        found = []
        seen = set()
        for row in reader:
            for side in ("借方", "貸方"):
                if kind == "sub":
                    item = dict(zip(SCHEMAS["relation"][1], [_text(row.get(side + suffix)) for suffix in ("科目", "補助", "補助科目名")]))
                    # A partial supplementary item cannot form a valid relation.
                    if not item["sub_code"] or not item["sub_name"]:
                        continue
                else:
                    suffix = "科目" if kind == "account" else "部門"
                    item = {"code": _text(row.get(side + suffix)), "name": _text(row.get(side + suffix + "名"))}
                    if not all(item.values()):
                        continue
                key = tuple(item.values())
                if key not in seen:
                    seen.add(key)
                    found.append(item)
        return found


def _plan(root, kind):
    if kind not in ("account", "department", "sub"):
        raise MasterUpdateError("更新対象を選択してください。")
    fields, current = _load(root, kind)
    found = _found(root, kind)
    added, conflicts = [], []
    updates = {}
    if kind != "sub":
        names_by_code = {}
        for row in current:
            names_by_code.setdefault(_text(row["code"]), set()).add(_text(row["name"]))
        unchanged = 0
        for item in found:
            names = names_by_code.get(item["code"], set())
            if names - {item["name"]}:
                conflicts.append({**item, "reason": "同じコードに別の名称があります。", "existing_names": sorted(names)})
            elif item["name"] in names:
                unchanged += 1
            else:
                added.append({**item, **({"category": ""} if kind == "account" else {})})
                names_by_code.setdefault(item["code"], set()).add(item["name"])
        if added:
            if kind == "account" and "category" not in fields:
                fields = fields + ["category"]
            updates[root / SCHEMAS[kind][0]] = _bytes(fields, current + added)
        result = {"current_count": len(current), "found_count": len(found), "added_count": len(added),
                  "unchanged_count": unchanged, "added_items": added}
    else:
        relation_fields, relations = _load(root, "relation")
        _, accounts = _load(root, "account")
        parent_codes = {_text(r["code"]) for r in accounts}
        pairs = {(_text(r["code"]), _text(r["name"])) for r in current}
        relation_names = {}
        for r in relations:
            relation_names.setdefault((_text(r["account_code"]), _text(r["sub_code"])), set()).add(_text(r["sub_name"]))
        new_relations, unchanged = [], 0
        for item in found:
            key = (item["account_code"], item["sub_code"])
            names = relation_names.get(key, set())
            if item["account_code"] not in parent_codes:
                conflicts.append({**item, "reason": "親科目が未登録です。科目マスターを先に作成・更新してください。"})
                continue
            if names - {item["sub_name"]}:
                conflicts.append({**item, "reason": "同じ親科目・補助コードに別の名称があります。"})
                continue
            pair = (item["sub_code"], item["sub_name"])
            sub_exists = pair in pairs
            relation_exists = item["sub_name"] in names
            if sub_exists and relation_exists:
                unchanged += 1
            if not sub_exists:
                added.append({"code": pair[0], "name": pair[1]})
                pairs.add(pair)
            if not relation_exists:
                new_relations.append(item)
                relation_names.setdefault(key, set()).add(item["sub_name"])
        if added:
            updates[root / SCHEMAS["sub"][0]] = _bytes(fields, current + added)
        if new_relations:
            updates[root / SCHEMAS["relation"][0]] = _bytes(relation_fields, relations + new_relations)
        result = {"current_count": len(current), "relation_current_count": len(relations),
                  "found_count": len(found), "added_count": len(added) + len(new_relations),
                  "unchanged_count": unchanged, "added_items": added,
                  "sub_added_count": len(added), "relation_added_count": len(new_relations),
                  "relation_added_items": new_relations}
    return {**result, "kind": kind, "conflict_count": len(conflicts), "conflicts": conflicts}, updates


def update_masters(directory, kind, *, execute=False):
    with MASTER_LOCK:
        result, updates = _plan(Path(directory), kind)
        result["applied"] = False
        if execute and not result["conflicts"]:
            _save_batch(updates)
            result["applied"] = True
        return result


def add_account(directory, code, name, category, *, add_to_payment=False):
    code, name, category = map(_text, (code, name, category))
    if not code or not name or category not in CATEGORIES:
        raise MasterUpdateError("科目コード・科目名・分類を確認してください。")
    root = Path(directory)
    with MASTER_LOCK:
        fields, accounts = _load(root, "account")
        same_code = [r for r in accounts if _text(r["code"]) == code]
        if any(_text(r["name"]) != name for r in same_code):
            raise MasterUpdateError("同じ科目コードに別の名称が登録されています。")
        added = not same_code
        after = accounts + ([{"code": code, "name": name, "category": category}] if added else [])
        updates = {}
        payment_added = False
        if add_to_payment:
            codes = {_text(r["code"]) for r in after if _text(r["name"]) == name}
            if len(codes) != 1:
                raise MasterUpdateError("同じ名称に複数の科目コードがあるため入金科目候補へ追加できません。")
            if name.casefold() == "nan":
                raise MasterUpdateError("入金科目候補として利用できない名称です。")
            payment_fields, payments = _load(root, "payment")
            if name not in {_text(r["科目"]) for r in payments}:
                payment_added = True
                updates[root / SCHEMAS["payment"][0]] = _bytes(payment_fields, payments + [{"科目": name}])
        if added:
            if "category" not in fields:
                fields = fields + ["category"]
            updates = {root / SCHEMAS["account"][0]: _bytes(fields, after), **updates}
        _save_batch(updates)
        return {"account_added": added, "payment_added": payment_added,
                "message": "科目候補を追加しました。" if added else "科目候補は登録済みです。"}
