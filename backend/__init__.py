"""
backend

Translation backend package.

Public architecture
-------------------

    TranslationRequest
            ↓
    TranslationPipeline
            ↓
    ┌───────────────────────────┐
    │                           │
    ↓                           ↓
LLMTranslationResponse    DashboardResponse

The backend is responsible only for:

1. Receiving a validated request.
2. Selecting the configured processing modules.
3. Executing the pipeline.
4. Returning a validated response.

The Dictionary and LLM subsystems are treated as
independent black-box components.
"""

from .pipeline import (
    PipelineConfigurationError,
    PipelineError,
    PipelineExecutionError,
    PipelineInputError,
    PipelineMode,
    TranslationPipeline,
    create_pipeline,
)

from .schema import (
    DashboardEntryResponse,
    DashboardResponse,
    DashboardEntryType,
    LLMTranslationResponse,
    TranslationMode,
    TranslationRequest,
    TranslationResponse,
)


__all__ = [
    # ============================================================
    # Schema
    # ============================================================

    "TranslationMode",
    "TranslationRequest",

    # Common response type
    "TranslationResponse",

    # LLM output
    "LLMTranslationResponse",

    # Dashboard / Dictionary output
    "DashboardEntryType",
    "DashboardEntryResponse",
    "DashboardResponse",

    # ============================================================
    # Pipeline
    # ============================================================

    "PipelineError",
    "PipelineConfigurationError",
    "PipelineInputError",
    "PipelineExecutionError",
    "PipelineMode",
    "TranslationPipeline",
    "create_pipeline",
]