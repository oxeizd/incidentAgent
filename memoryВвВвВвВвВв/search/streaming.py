from __future__ import annotations


DEFAULT_SEARCH_BATCH_SIZE = 500
MAX_SEARCH_BATCH_SIZE = 5_000


def validate_batch_size(value: int) -> int:
    """
    Validates bounded DB fetch size for structured search streaming.

    Batch size controls peak memory used by one fetchmany() call. Search
    engines yield batches, and SearchResultWriter persists each batch before
    requesting the next one.
    """
    if value < 1:
        raise ValueError("batch_size must be at least 1")

    return min(value, MAX_SEARCH_BATCH_SIZE)