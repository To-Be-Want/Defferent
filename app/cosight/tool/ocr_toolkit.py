# Copyright 2025 ZTE Corporation.
# All Rights Reserved.

"""Local OCR support for images and scanned PDF files.

RapidOCR is imported lazily so the rest of Co-Sight can still start when the
optional OCR runtime has not been installed yet.  The engine is shared because
loading its ONNX models for every page is expensive.
"""

from __future__ import annotations

from io import BytesIO
import math
import os
from pathlib import Path
import threading
from typing import Any, Iterable
from urllib.parse import urlparse

import requests

import logging

logger = logging.getLogger(__name__)


class OCRUnavailableError(RuntimeError):
    """Raised when the local OCR dependencies are unavailable."""


class OCRToolkit:
    """Extract Chinese and English text from images and scanned PDFs."""

    IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp", ".gif"}
    _engine: Any = None
    _engine_lock = threading.Lock()
    _inference_lock = threading.Lock()

    def __init__(self) -> None:
        self.min_confidence = float(os.getenv("OCR_MIN_CONFIDENCE", "0.35"))
        self.pdf_dpi = int(os.getenv("OCR_PDF_DPI", "200"))
        self.max_pdf_pages = int(os.getenv("OCR_MAX_PDF_PAGES", "100"))
        self.native_text_min_chars = int(os.getenv("OCR_NATIVE_TEXT_MIN_CHARS", "10"))

    @classmethod
    def _get_engine(cls):
        if cls._engine is None:
            with cls._engine_lock:
                if cls._engine is None:
                    try:
                        from rapidocr import RapidOCR
                    except ImportError as exc:
                        raise OCRUnavailableError(
                            "OCR 依赖未安装，请执行：pip install rapidocr onnxruntime"
                        ) from exc
                    logger.info("Initializing local RapidOCR engine")
                    cls._engine = RapidOCR()
        return cls._engine

    @staticmethod
    def _normalise_result(result: Any) -> list[tuple[str, float]]:
        """Support both current RapidOCR output objects and legacy tuples."""
        if result is None:
            return []

        texts = getattr(result, "txts", None)
        scores = getattr(result, "scores", None)
        if texts is not None:
            scores = scores if scores is not None else [1.0] * len(texts)
            return [(str(text), float(score)) for text, score in zip(texts, scores)]

        # Older RapidOCR releases returned ``(items, elapsed)``. Each item is
        # ``[box, text, confidence]``.
        items = result[0] if isinstance(result, tuple) and len(result) == 2 else result
        parsed: list[tuple[str, float]] = []
        if isinstance(items, Iterable) and not isinstance(items, (str, bytes, dict)):
            for item in items:
                if isinstance(item, (list, tuple)) and len(item) >= 2:
                    text = item[1]
                    score = item[2] if len(item) >= 3 else 1.0
                    parsed.append((str(text), float(score)))
        return parsed

    def recognize_image(self, image: Any) -> str:
        """Recognize text in a path, NumPy image, or encoded image bytes."""
        source = image
        if isinstance(image, (str, Path)):
            image = self._local_path(str(image)).read_bytes()
        if isinstance(image, (bytes, bytearray)):
            try:
                import numpy as np
                from PIL import Image

                with Image.open(BytesIO(image)) as opened:
                    if opened.width * opened.height > 25_000_000:
                        raise ValueError("图片像素过大，请缩小到2500万像素以内")
                    source = np.asarray(opened.convert("RGB"))
            except Exception as exc:
                raise ValueError(f"无法读取图片数据：{exc}") from exc

        # A single engine is shared by concurrent agent tasks. Serializing
        # inference also keeps less capable ONNX providers predictable.
        with self._inference_lock:
            result = self._get_engine()(source)
        lines = [
            text.strip()
            for text, score in self._normalise_result(result)
            if text.strip() and score >= self.min_confidence
        ]
        return "\n".join(lines)

    def _has_meaningful_native_text(self, text: str) -> bool:
        visible_chars = sum(ch.isalnum() or "\u4e00" <= ch <= "\u9fff" for ch in text)
        return visible_chars >= self.native_text_min_chars

    def extract_pdf_text(self, pdf_source: str | bytes) -> str:
        """Use a PDF text layer when available and OCR image-only pages."""
        from pypdf import PdfReader

        if not isinstance(pdf_source, bytes):
            pdf_source = str(self._local_path(pdf_source))
        reader_source = BytesIO(pdf_source) if isinstance(pdf_source, bytes) else pdf_source
        reader = PdfReader(reader_source)
        if len(reader.pages) > 1000:
            raise ValueError("PDF超过1000页，请拆分后上传")
        native_pages = [(page.extract_text() or "").strip() for page in reader.pages]
        page_count = len(native_pages)

        pages_needing_ocr = [
            index for index, text in enumerate(native_pages)
            if not self._has_meaningful_native_text(text)
        ]
        if not pages_needing_ocr:
            return "\n\n".join(f"[第 {i+1} 页]\n{text}" for i,text in enumerate(native_pages))

        try:
            import fitz
        except ImportError as exc:
            raise OCRUnavailableError("扫描版 PDF OCR 需要 PyMuPDF，请执行：pip install PyMuPDF") from exc

        document = (
            fitz.open(stream=pdf_source, filetype="pdf")
            if isinstance(pdf_source, bytes)
            else fitz.open(pdf_source)
        )
        try:
            page_limit = min(page_count, self.max_pdf_pages)
            scale = self.pdf_dpi / 72.0
            output_pages: list[str] = []

            for index, native_text in enumerate(native_pages):
                page_number = index + 1
                if native_text and self._has_meaningful_native_text(native_text):
                    page_text = native_text
                elif index < page_limit:
                    page = document[index]
                    page_scale = self._pdf_page_scale(page.rect.width, page.rect.height, scale)
                    if page_scale < scale:
                        logger.info("PDF page %s: reducing OCR resolution from %.1f to %.1f DPI",
                                    page_number, scale * 72, page_scale * 72)
                    pixmap = page.get_pixmap(matrix=fitz.Matrix(page_scale, page_scale), alpha=False)
                    page_text = self.recognize_image(pixmap.tobytes("png"))
                else:
                    page_text = "[超过 OCR_MAX_PDF_PAGES 限制，未识别本页]"

                if page_text:
                    output_pages.append(f"[第 {page_number} 页]\n{page_text}")

            return "\n\n".join(output_pages)
        finally:
            document.close()

    @staticmethod
    def _pdf_page_scale(width: float, height: float, scale: float) -> float:
        """Keep aspect ratio and bound rounded raster dimensions before allocating."""
        limit = 25_000_000
        if not all(math.isfinite(v) and v > 0 for v in (width, height, scale)):
            raise ValueError('PDF页面尺寸或识别分辨率无效')
        def fits(value):
            return math.ceil(width * value) * math.ceil(height * value) <= limit
        if fits(scale):
            return scale
        low, high = 0.0, min(scale, math.sqrt(limit / (width * height)))
        # Account for raster rounding (also on very wide/narrow pages).
        for _ in range(48):
            middle = (low + high) / 2
            if fits(middle):
                low = middle
            else:
                high = middle
        return low

    @staticmethod
    def _local_path(value: str) -> Path:
        from user_context import current_user, user_root
        path = Path(value).resolve()
        if current_user.get() and not path.is_relative_to((user_root() / "upload_files").resolve()):
            raise PermissionError("只能识别当前账号上传的资料")
        if not path.is_file():
            raise FileNotFoundError("找不到待识别的文件")
        # Match the local upload limit; PDFs are opened from disk, not downloaded
        # into a second in-memory copy. Remote downloads retain their own bound.
        if path.stat().st_size > 500 * 1024 * 1024:
            raise ValueError("识别文件不能超过500MB")
        return path

    @staticmethod
    def _is_url(value: str) -> bool:
        parsed = urlparse(value)
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)

    def extract_text(self, file_path_or_url: str) -> str:
        """Extract text from a supported local image/PDF or HTTP(S) URL."""
        source: str | bytes = file_path_or_url
        content_type = ""
        if self._is_url(file_path_or_url):
            from user_context import current_user
            if current_user.get():
                raise PermissionError("请先上传图片或PDF，再进行文字识别")
            # Legacy local agents may read remote public documents; bound downloads.
            with requests.get(file_path_or_url, timeout=60, stream=True) as response:
                response.raise_for_status()
                chunks, size = [], 0
                for chunk in response.iter_content(1024 * 1024):
                    size += len(chunk)
                    if size > 50 * 1024 * 1024:
                        raise ValueError("识别文件不能超过50MB")
                    chunks.append(chunk)
                source = b"".join(chunks)
                content_type = response.headers.get("Content-Type", "").lower()
        else:
            source = str(self._local_path(file_path_or_url))

        suffix = Path(urlparse(file_path_or_url).path).suffix.lower()
        if suffix == ".pdf" or "application/pdf" in content_type:
            return self.extract_pdf_text(source)
        if suffix in self.IMAGE_EXTENSIONS or content_type.startswith("image/"):
            return self.recognize_image(source)
        raise ValueError(f"OCR 不支持该文件类型：{suffix or content_type or '未知'}")

    def extract_text_for_agent(self, file_path_or_url: str) -> str:
        """Agent-facing wrapper that returns actionable errors as text."""
        try:
            text = self.extract_text(file_path_or_url)
            return text if text.strip() else "OCR 未识别到文字。"
        except Exception as exc:
            logger.error(f"OCR extraction failed: {exc}", exc_info=True)
            return f"OCR 识别失败：{exc}"
