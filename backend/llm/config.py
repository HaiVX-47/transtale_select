from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


DeviceName = Literal[
    "auto",
    "cpu",
    "gpu",
]


@dataclass(frozen=True, slots=True)
class LanguageTranslationConfig:
    """
    Cấu hình cho model dịch máy Anh → Việt.

    Pipeline:

        English text
        → tokenizer của model
        → MarianMT
        → Vietnamese text

    Module này không dùng:
        - tokenizer của dictionary mode;
        - lemmatization;
        - PhraseTrie;
        - word vocabulary;
        - prompt kiểu chatbot.
    """

    # ==========================================================
    # Model
    # ==========================================================

    # Model dịch chuyên dụng Anh → Việt.
    model_name: str = "Helsinki-NLP/opus-mt-en-vi"

    # Model này yêu cầu token chỉ định ngôn ngữ đầu ra.
    #
    # Ví dụ input thực tế gửi vào tokenizer:
    #
    #     >>vie<< She decided to give up.
    target_language_token: str = ">>vie<<"

    # ==========================================================
    # Device
    # ==========================================================

    # auto:
    #     Dùng CUDA nếu khả dụng, nếu không thì CPU.
    #
    # gpu:
    #     Bắt buộc phải có CUDA.
    #
    # cpu:
    #     Luôn chạy trên CPU.
    device: DeviceName = "auto"

    # Chuyển model sang float16 khi chạy bằng CUDA.
    # Giúp giảm VRAM và thường tăng tốc inference.
    use_float16_on_gpu: bool = True

    # ==========================================================
    # Input
    # ==========================================================

    # Subtitle thường ngắn nên 256 token là đủ.
    # Model gốc hỗ trợ tối đa 512 token.
    max_input_tokens: int = 256

    # Giới hạn ký tự để chặn input bất thường.
    max_input_characters: int = 5_000

    # Thu gọn khoảng trắng và xuống dòng trước khi dịch.
    #
    # "She   decided\nto leave."
    # →
    # "She decided to leave."
    normalize_whitespace: bool = True

    # ==========================================================
    # Generation
    # ==========================================================

    # Số token tối đa được sinh cho bản dịch.
    max_new_tokens: int = 256

    # Beam search.
    #
    # 1:
    #     Greedy decoding. Keeps CPU translation responsive.
    #
    # Larger values can improve some translations but multiply CPU work.
    #
    # 4:
    #     Chậm hơn nhưng có thể cho bản dịch tốt hơn.
    num_beams: int = 1

    # Điều chỉnh thiên hướng về độ dài bản dịch.
    length_penalty: float = 1.0

    # Cho phép dừng sớm khi beam search đã tìm được
    # các chuỗi hoàn chỉnh.
    early_stopping: bool = True

    # ==========================================================
    # Model loading
    # ==========================================================

    # False:
    #     Lần đầu có thể tải model từ Hugging Face.
    #
    # True:
    #     Chỉ sử dụng model đã tồn tại trong cache.
    local_files_only: bool = False

    def __post_init__(self) -> None:
        if not self.model_name.strip():
            raise ValueError(
                "model_name must not be empty."
            )

        if self.device not in {
            "auto",
            "cpu",
            "gpu",
        }:
            raise ValueError(
                "device must be 'auto', 'cpu', or 'gpu'."
            )

        if self.max_input_tokens < 1:
            raise ValueError(
                "max_input_tokens must be at least 1."
            )

        if self.max_input_tokens > 512:
            raise ValueError(
                "max_input_tokens must not exceed 512 "
                "for the selected MarianMT model."
            )

        if self.max_input_characters < 1:
            raise ValueError(
                "max_input_characters must be at least 1."
            )

        if self.max_new_tokens < 1:
            raise ValueError(
                "max_new_tokens must be at least 1."
            )

        if self.num_beams < 1:
            raise ValueError(
                "num_beams must be at least 1."
            )

        if self.length_penalty <= 0:
            raise ValueError(
                "length_penalty must be greater than 0."
            )


DEFAULT_LANGUAGE_TRANSLATION_CONFIG = (
    LanguageTranslationConfig()
)
