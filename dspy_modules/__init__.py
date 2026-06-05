"""DSPy-based prompt modules.

DSPy lets us define prompts as typed Signatures, then optionally compile them
against labeled examples for better few-shot ordering and instruction tuning.

Integration is split in two:
- Production path (`PostMortemClassifier.forward`): runs the Signature through
  the configured LM, returns typed output. Safe to use without compile.
- Compile path (`scripts/dspy_compile_post_mortem.py`): runs DSPy's
  BootstrapFewShot using historical labeled positions, persists the resulting
  optimized program as a learned_params row. Operator-triggered — burns API
  tokens during search.

When no compiled program exists, callers fall back to the bare Signature
(zero-shot Predict). When one exists, callers load it and use the optimized
demos automatically.
"""
