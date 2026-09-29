from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator


# ============================================================
# ENUM
# ============================================================

class TranslationMode(str, Enum):
    """
    Phương thức xử lý được backend hỗ trợ.
    """

    DICTIONARY = "dictionary"
    LLM = "llm"


# ============================================================
# REQUEST
# ============================================================

class TranslationRequest(BaseModel):
    """
    Request gửi từ frontend tới backend.

    Example:
    {
        "text": "sophisticated",
        "mode": "dictionary"
    }
    """

    text: str = Field(
        ...,
        description="Text được frontend lấy từ vùng người dùng bôi đen.",
        min_length=1,
        max_length=2000,
    )

    mode: TranslationMode = Field(
        default=TranslationMode.DICTIONARY,
        description="Phương thức xử lý.",
    )

    # --------------------------------------------------------
    # Validation: text
    # --------------------------------------------------------

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        # 1. Loại bỏ khoảng trắng đầu/cuối
        value = value.strip()

        # 2. Không cho phép text rỗng
        if not value:
            raise ValueError("Text cannot be empty.")

        # 3. Kiểm tra text chỉ chứa whitespace
        if value.isspace():
            raise ValueError("Text cannot contain only whitespace.")

        return value


# ============================================================
# DASHBOARD ENTRY
# ============================================================

DashboardEntryType = Literal["word", "phrase"]


class DashboardEntryResponse(BaseModel):
    """
    Một dictionary entry hoàn chỉnh dành cho Dashboard.

    Một entry giữ toàn bộ:
    - term
    - meanings
    - ipas
    - entry_type

    IPA có thể không tồn tại.
    Khi đó ipas = ().
    """

    term: str = Field(
        ...,
        description="Từ hoặc cụm từ được tìm thấy trong dictionary.",
    )

    meanings: tuple[str, ...] = Field(
        ...,
        description="Toàn bộ meanings của dictionary entry.",
    )

    ipas: tuple[str, ...] = Field(
        default=(),
        description=(
            "Toàn bộ IPA của dictionary entry. "
            "Có thể rỗng nếu dictionary không cung cấp IPA."
        ),
    )

    entry_type: DashboardEntryType = Field(
        ...,
        description="Loại entry: word hoặc phrase.",
    )

    # --------------------------------------------------------
    # Validation
    # --------------------------------------------------------

    @field_validator("term")
    @classmethod
    def validate_term(cls, value: str) -> str:
        value = value.strip()

        if not value:
            raise ValueError("Term cannot be empty.")

        return value

    @field_validator("meanings")
    @classmethod
    def validate_meanings(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        if not value:
            raise ValueError(
                "Dashboard entry must contain at least one meaning."
            )

        cleaned = tuple(
            meaning.strip()
            for meaning in value
            if meaning.strip()
        )

        if not cleaned:
            raise ValueError(
                "Dashboard entry must contain at least one valid meaning."
            )

        return cleaned

    @field_validator("ipas")
    @classmethod
    def validate_ipas(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        """
        IPA là optional.

        - () được chấp nhận.
        - Các IPA rỗng/whitespace sẽ bị loại bỏ.
        - Nếu dictionary không có IPA thì kết quả cuối cùng là ().
        """

        cleaned = tuple(
            ipa.strip()
            for ipa in value
            if ipa.strip()
        )

        return cleaned


# ============================================================
# LLM RESPONSE
# ============================================================

class LLMTranslationResponse(BaseModel):
    """
    Output của LLM.

    Mỗi phần tử trong translation_lines tương ứng
    với đúng một dòng của input.

    Pipeline không join các dòng lại.
    """

    text: str = Field(
        ...,
        description="Text gốc được dịch.",
    )

    translation_lines: tuple[str, ...] = Field(
        ...,
        description=(
            "Các dòng kết quả dịch. "
            "translation_lines[i] tương ứng với dòng thứ i "
            "của text."
        ),
    )

    mode: Literal[TranslationMode.LLM] = Field(
        default=TranslationMode.LLM,
        description="Phương thức xử lý.",
    )

    # --------------------------------------------------------
    # Validation: text
    # --------------------------------------------------------

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        value = value.strip()

        if not value:
            raise ValueError("Text cannot be empty.")

        return value

    # --------------------------------------------------------
    # Validation: translation_lines
    # --------------------------------------------------------

    @field_validator("translation_lines")
    @classmethod
    def validate_translation_lines(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:

        if not value:
            raise ValueError(
                "translation_lines cannot be empty."
            )

        # Không strip toàn bộ collection theo cách làm mất
        # các dòng rỗng, vì dòng rỗng cần được preserve.
        cleaned = tuple(
            line.strip()
            for line in value
        )

        # Chỉ cần ít nhất một dòng có nội dung.
        if not any(cleaned):
            raise ValueError(
                "translation_lines must contain at least "
                "one non-empty line."
            )

        return cleaned


# ============================================================
# DASHBOARD RESPONSE
# ============================================================

class DashboardResponse(BaseModel):
    """
    Output của Dictionary dành cho Dashboard.

    Không ép Dictionary thành một translation string.

    Mỗi dictionary item được giữ riêng dưới dạng
    DashboardEntryResponse.
    """

    entries: tuple[DashboardEntryResponse, ...] = Field(
        ...,
        description=(
            "Danh sách dictionary entries được tìm thấy, "
            "đã deduplicate và sort."
        ),
    )

    mode: Literal[TranslationMode.DICTIONARY] = Field(
        default=TranslationMode.DICTIONARY,
        description="Phương thức xử lý.",
    )

    # --------------------------------------------------------
    # Validation
    # --------------------------------------------------------

    @field_validator("entries")
    @classmethod
    def validate_entries(
        cls,
        value: tuple[DashboardEntryResponse, ...],
    ) -> tuple[DashboardEntryResponse, ...]:
        if not value:
            raise ValueError(
                "Dashboard response must contain at least one entry."
            )

        return value


# ============================================================
# UNIFIED RESPONSE
# ============================================================

TranslationResponse = Annotated[
    LLMTranslationResponse | DashboardResponse,
    Field(discriminator="mode"),
]


# ============================================================
# PUBLIC API
# ============================================================

__all__ = [
    "TranslationMode",
    "TranslationRequest",
    "DashboardEntryType",
    "DashboardEntryResponse",
    "LLMTranslationResponse",
    "DashboardResponse",
    "TranslationResponse",
]