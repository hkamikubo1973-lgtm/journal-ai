from dataclasses import dataclass
from typing import Any, Optional, Protocol
import os


@dataclass(frozen=True)
class OcrResult:
    search_text: str = ""
    amount: Optional[int] = None
    memo: str = ""
    raw_text: str = ""
    confidence: Optional[float] = None
    amount_candidates: Optional[list[Any]] = None
    search_text_candidates: Optional[list[str]] = None
    memo_candidates: Optional[list[str]] = None
    invoice_registration_number: Optional[str] = None
    invoice_number_present: bool = False
    invoice_number_valid_format: bool = False
    invoice_number_verified: bool = False
    invoice_number_candidates: Optional[list[str]] = None
    warnings: Optional[list[str]] = None


class OcrGateway(Protocol):
    def analyze(
        self,
        content: bytes,
        filename: str,
        mime_type: str,
    ) -> OcrResult:
        ...


class DummyOcrGateway:
    def analyze(
        self,
        content: bytes,
        filename: str,
        mime_type: str,
    ) -> OcrResult:
        return OcrResult(
            search_text="ダミーOCR 太陽インキ 請求書",
            amount=194400,
            memo="ダミーOCR 5月分請求 194400円",
            raw_text="ダミーOCR 太陽インキ 請求書 5月分請求 194,400円",
            confidence=1.0,
            amount_candidates=[194400, {"amount": 194400, "label": "税込候補"}],
            search_text_candidates=["ダミーOCR 太陽インキ 請求書"],
            memo_candidates=["ダミーOCR 5月分請求 194400円"],
            invoice_registration_number="T1234567890123",
            invoice_number_present=True,
            invoice_number_valid_format=True,
            invoice_number_verified=False,
            invoice_number_candidates=["T1234567890123"],
            warnings=[],
        )


class PaddleOcrGateway:
    """
    AIサーバーの OCR API を呼び出す Gateway。

    AIサーバー側:
        POST http://10.0.0.32:8000/ocr

    返却期待:
        {
          "search_text": "...",
          "amount": 5210,
          "memo": "...",
          "raw_text": "...",
          "confidence": 0.9331,
          "amount_candidates": [5210, {"amount": 5210, "source": "..."}],
          "search_text_candidates": ["..."],
          "memo_candidates": ["..."],
          "invoice_number_candidates": ["T1234567890123"],
          "engine": "paddleocr",
          "warnings": []
        }
    """

    def __init__(self, endpoint_url: Optional[str] = None, timeout: int = 300):
        self.endpoint_url = endpoint_url or os.environ.get(
            "AI_OCR_API_URL",
            "http://10.0.0.32:8000/ocr",
        )
        self.timeout = int(os.environ.get("AI_OCR_TIMEOUT", timeout))

    def analyze(
        self,
        content: bytes,
        filename: str,
        mime_type: str,
    ) -> OcrResult:
        if not content:
            return OcrResult(
                search_text="",
                amount=None,
                memo="",
                raw_text="OCR対象ファイルが空です。",
                confidence=0.0,
                warnings=["OCR対象ファイルが空です。"],
            )

        try:
            import requests
        except ImportError:
            return OcrResult(
                search_text="",
                amount=None,
                memo="",
                raw_text="requests が未インストールです。python -m pip install requests を実行してください。",
                confidence=0.0,
                warnings=["requests が未インストールです。"],
            )

        upload_filename = filename or "upload.jpg"
        upload_mime_type = mime_type or "application/octet-stream"

        try:
            response = requests.post(
                self.endpoint_url,
                files={
                    "file": (
                        upload_filename,
                        content,
                        upload_mime_type,
                    )
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()

        except Exception as e:
            message = f"OCR API接続エラー: {type(e).__name__}: {e}"
            return OcrResult(
                search_text="",
                amount=None,
                memo="",
                raw_text=message,
                confidence=0.0,
                warnings=[message],
            )

        amount = data.get("amount")
        if amount == "":
            amount = None

        try:
            if amount is not None:
                amount = int(amount)
        except Exception:
            amount = None

        confidence = data.get("confidence")
        try:
            if confidence is not None:
                confidence = float(confidence)
        except Exception:
            confidence = None

        search_text = str(data.get("search_text") or "")
        memo = str(data.get("memo") or search_text)
        raw_text = str(data.get("raw_text") or "")

        amount_candidates = self._normalize_amount_candidates(
            data.get("amount_candidates"),
            amount,
        )
        search_text_candidates = self._normalize_text_candidates(
            data.get("search_text_candidates"),
            search_text,
        )
        memo_candidates = self._normalize_text_candidates(
            data.get("memo_candidates"),
            memo,
        )

        invoice_registration_number = data.get("invoice_registration_number")
        if invoice_registration_number is not None:
            invoice_registration_number = str(invoice_registration_number)

        invoice_number_present = bool(data.get("invoice_number_present", False))
        invoice_number_valid_format = bool(data.get("invoice_number_valid_format", False))
        invoice_number_verified = bool(data.get("invoice_number_verified", False))

        invoice_number_candidates = data.get("invoice_number_candidates") or []
        if not isinstance(invoice_number_candidates, list):
            invoice_number_candidates = [str(invoice_number_candidates)]
        else:
            invoice_number_candidates = [str(x) for x in invoice_number_candidates]

        warnings = self._normalize_text_candidates(data.get("warnings"), "")

        return OcrResult(
            search_text=search_text,
            amount=amount,
            memo=memo,
            raw_text=raw_text,
            confidence=confidence,
            amount_candidates=amount_candidates,
            search_text_candidates=search_text_candidates,
            memo_candidates=memo_candidates,
            invoice_registration_number=invoice_registration_number,
            invoice_number_present=invoice_number_present,
            invoice_number_valid_format=invoice_number_valid_format,
            invoice_number_verified=invoice_number_verified,
            invoice_number_candidates=invoice_number_candidates,
            warnings=warnings,
        )

    @staticmethod
    def _as_list(value: Any) -> list[Any]:
        if value is None or value == "":
            return []
        if isinstance(value, list):
            return value
        if isinstance(value, tuple):
            return list(value)
        return [value]

    @classmethod
    def _normalize_text_candidates(
        cls,
        value: Any,
        fallback: str,
    ) -> list[str]:
        candidates = [
            str(item).strip()
            for item in cls._as_list(value)
            if str(item).strip()
        ]
        if not candidates and fallback.strip():
            candidates = [fallback.strip()]
        return candidates

    @classmethod
    def _normalize_amount_candidates(
        cls,
        value: Any,
        fallback: Optional[int],
    ) -> list[Any]:
        candidates = cls._as_list(value)
        normalized: list[Any] = []

        for item in candidates:
            if isinstance(item, dict):
                normalized.append(item)
                continue

            try:
                normalized.append(int(item))
            except Exception:
                text = str(item).strip()
                if text:
                    normalized.append(text)

        if not normalized and fallback is not None:
            normalized = [fallback]

        return normalized
