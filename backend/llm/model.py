"""
English -> Vietnamese MarianMT translation.

The module boundary is defined by backend.schema:

    TranslationRequest
        ->
    LanguageTranslator
        ->
    TranslationResponse

The translator does not know about dictionary mode, OCR objects,
bounding boxes, or frontend objects.

The model is loaded once and reused.
"""

from __future__ import annotations

from dataclasses import dataclass

import torch
from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

from transformers import (
    MarianMTModel,
    MarianTokenizer,
)

from ..schema import TranslationRequest

from .config import (
    DEFAULT_LANGUAGE_TRANSLATION_CONFIG,
    LanguageTranslationConfig,
)
from .preprocessor import (
    LanguagePreprocessorError,
    LanguageTextPreprocessor,
)


class LanguageTranslationError(RuntimeError):
    """Base runtime error for LLM translation."""


class LanguageModelLoadError(LanguageTranslationError):
    """Unable to load or initialize the translation model."""


class LanguageTranslationInputError(ValueError):
    """Input does not satisfy the translator contract."""


class LanguageTranslationInferenceError(LanguageTranslationError):
    """Inference or decoding failed."""


class LanguageTranslator:
    """
    Stateful English -> Vietnamese MarianMT translator.

    Public boundary:

        TranslationRequest -> TranslationResponse

    The model is loaded once during construction and reused for
    subsequent requests.
    """

    def __init__(
        self,
        config: LanguageTranslationConfig | None = None,
    ) -> None:
        self.config = (
            config
            or DEFAULT_LANGUAGE_TRANSLATION_CONFIG
        )

        self._device = self._resolve_device()
        self._preprocessor = LanguageTextPreprocessor()

        self._tokenizer = None
        self._model = None

        self._load_model()

    @property
    def device(self) -> str:
        return str(self._device)

    @property
    def model_name(self) -> str:
        return self.config.model_name

    # ==========================================================
    # Public API
    # ==========================================================

    def translate(
        self,
        request: TranslationRequest,
    ) -> str:
        """
        Translate one validated application request.

        Input:
            TranslationRequest

        Output:
            str
        """
        if not isinstance(request, TranslationRequest):
            raise LanguageTranslationInputError(
                "request must be a TranslationRequest, "
                f"received {type(request).__name__}."
            )

        source_text = request.text

        # TranslationRequest already validates non-empty text.
        # The extra validation here protects this module if called
        # independently from FastAPI.
        if not isinstance(source_text, str):
            raise LanguageTranslationInputError(
                "request.text must be a string."
            )

        if "\x00" in source_text:
            raise LanguageTranslationInputError(
                "request.text must not contain null characters."
            )

        if len(source_text) > self.config.max_input_characters:
            raise LanguageTranslationInputError(
                "request.text exceeds the configured maximum of "
                f"{self.config.max_input_characters} characters."
            )

        processed_text = self._preprocess(source_text)

        # Schema guarantees a non-empty text, but preprocessing could
        # theoretically remove meaningful content in a future version.
        if not processed_text:
            raise LanguageTranslationInputError(
                "Preprocessing produced empty text."
            )

        model_input = self._add_language_token(
            processed_text
        )

        try:
            encoded = self._tokenizer(
                model_input,
                return_tensors="pt",
                truncation=True,
                max_length=self.config.max_input_tokens,
                padding=False,
            )

            encoded = {
                name: tensor.to(self._device)
                for name, tensor in encoded.items()
            }

            with torch.inference_mode():
                generation_options = {
                    "max_new_tokens": self.config.max_new_tokens,
                    "num_beams": self.config.num_beams,
                    "length_penalty": self.config.length_penalty,
                    "do_sample": False,
                }
                if self.config.num_beams > 1:
                    generation_options["early_stopping"] = (
                        self.config.early_stopping
                    )

                generated_ids = self._model.generate(
                    **encoded,
                    **generation_options,
                )

            translated_text = self._tokenizer.batch_decode(
                generated_ids,
                skip_special_tokens=True,
                clean_up_tokenization_spaces=True,
            )[0]

            translated_text = self._clean_output(
                translated_text
            )

            if not translated_text:
                raise LanguageTranslationInferenceError(
                    "The translation model returned empty output."
                )

        except LanguageTranslationError:
            raise
        except Exception as exc:
            raise LanguageTranslationInferenceError(
                "The translation model could not translate "
                "the supplied text."
            ) from exc

        return translated_text

    def translate_text(
        self,
        request: TranslationRequest,
    ) -> str:
        """Convenience API returning translated text."""
        return self.translate(request)

    def __call__(
        self,
        request: TranslationRequest,
    ) -> str:
        return self.translate(request)

    # ==========================================================
    # Model loading
    # ==========================================================

    def _load_model(self) -> None:
        # Prefer a complete local snapshot first. Hugging Face otherwise
        # checks the remote revision with HEAD requests even when the model
        # weights are already cached; a broken TLS connection can stall every
        # first translation while retrying those requests.
        local_error: Exception | None = None
        if not self.config.local_files_only:
            try:
                self._load_model_files(local_files_only=True)
                return
            except Exception as exc:
                local_error = exc

        try:
            self._load_model_files(
                local_files_only=self.config.local_files_only,
            )
        except Exception as exc:
            raise LanguageModelLoadError(
                f"Could not load translation model "
                f"'{self.config.model_name}'. "
                f"Original error: {type(exc).__name__}: {exc}"
                + (
                    f" Cached copy was unavailable: {local_error}"
                    if local_error is not None
                    else ""
                )
            ) from exc

    def _load_model_files(self, *, local_files_only: bool) -> None:
        self._tokenizer = MarianTokenizer.from_pretrained(
            self.config.model_name,
            local_files_only=local_files_only,
        )

        self._model = MarianMTModel.from_pretrained(
            self.config.model_name,
            local_files_only=local_files_only,
        )
        # Marian's bundled generation config has max_length=512. This app
        # controls output length with max_new_tokens; clear the inherited
        # value so Transformers does not warn about two competing limits.
        if getattr(self._model, "generation_config", None) is not None:
            self._model.generation_config.max_length = None
        self._model.to(self._device)

        if (
            self._device.type == "cuda"
            and self.config.use_float16_on_gpu
        ):
            self._model.half()

        self._model.eval()

    def _resolve_device(self) -> torch.device:
        if self.config.device == "cpu":
            return torch.device("cpu")

        if self.config.device == "gpu":
            if not torch.cuda.is_available():
                raise LanguageModelLoadError(
                    "device='gpu' was requested, but CUDA "
                    "is not available."
                )

            return torch.device("cuda")

        if torch.cuda.is_available():
            return torch.device("cuda")

        return torch.device("cpu")

    # ==========================================================
    # Text processing
    # ==========================================================

    def _preprocess(self, text: str) -> str:
        try:
            return self._preprocessor.preprocess(text)
        except LanguagePreprocessorError as exc:
            raise LanguageTranslationInputError(
                str(exc)
            ) from exc

    def _add_language_token(self, text: str) -> str:
        language_token = (
            self.config.target_language_token.strip()
        )

        if not language_token:
            return text

        return f"{language_token} {text}"

    @staticmethod
    def _clean_output(text: str) -> str:
        return text.strip()

__all__ = [
    "LanguageTranslationError",
    "LanguageModelLoadError",
    "LanguageTranslationInputError",
    "LanguageTranslationInferenceError",
    "LanguageTranslator",
]
