"""token_log.py -- AI token usage telemetry."""
from database import db_conn


def log_token_usage(module: str, model: str, input_tokens: int, output_tokens: int,
                    cached_tokens: int = 0, cache_creation_tokens: int = 0) -> None:
    """Record API token usage to the token_usage table.

    Args:
        cached_tokens: cache_read_input_tokens — served from cache at 0.1x input cost.
        cache_creation_tokens: cache_creation_input_tokens — written to cache at 1.25x
            input cost. Was silently dropped before 2026-05-31 fix; ~35% of spend.
    """
    try:
        with db_conn() as conn:
            conn.execute(
                "INSERT INTO token_usage "
                "(module, model, input_tokens, output_tokens, cached_tokens, cache_creation_tokens) "
                "VALUES (?,?,?,?,?,?)",
                (module, model, input_tokens, output_tokens, cached_tokens, cache_creation_tokens)
            )
            conn.commit()
    except Exception:
        pass  # never let logging errors break the calling code
