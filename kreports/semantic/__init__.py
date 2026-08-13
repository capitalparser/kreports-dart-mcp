"""Shared, fail-closed definitions for financial metrics and cached datasets."""

from kreports.semantic.datasets import DatasetDefinition, dataset_definition
from kreports.semantic.metrics import MetricDefinition, metric_definition

__all__ = (
    "DatasetDefinition",
    "MetricDefinition",
    "dataset_definition",
    "metric_definition",
)
