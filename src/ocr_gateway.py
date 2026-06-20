from dataclasses import dataclass
from typing import Optional, Protocol


@dataclass(frozen=True)
class OcrResult:
    search_text: str = ""
    amount: Optional[int] = None
    memo: str = ""
    raw_text: str = ""
    confidence: Optional[float] = None


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
            search_text="太陽インキ製造㈱",
            amount=194400,
            memo="5月分請求",
            raw_text="太陽インキ製造㈱ 5月分請求 194,400円",
            confidence=1.0,
        )


class PaddleOcrGateway:
    def analyze(
        self,
        content: bytes,
        filename: str,
        mime_type: str,
    ) -> OcrResult:
        raise NotImplementedError
