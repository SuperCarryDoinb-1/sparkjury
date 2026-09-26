from pathlib import Path

from sparkjury.models.trace import Trace, TraceSource


def load(path: str | Path, source: TraceSource | str) -> list[Trace]:
    """Load traces from a file using the adapter matching `source`."""
    src = TraceSource(source)
    if src == TraceSource.TAU2:
        from sparkjury.adapters.tau2 import load_tau2

        return load_tau2(path)
    if src == TraceSource.OTEL:
        from sparkjury.adapters.otel import load_otel

        return load_otel(path)
    raise ValueError(f"no adapter for source {src!r}")


__all__ = ["load"]
