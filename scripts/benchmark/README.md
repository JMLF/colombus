# Sequence-matching benchmarks

## Scripts:

- seed.py: generates 1000 synthetic notebooks with known planted sequences.
- query.py: runs a custom query (--steps), or the built-in correctness and timing battery (--test).

```
uv run python scripts/benchmark/seed.py --reset
uv run python scripts/benchmark/query.py --test
uv run python scripts/benchmark/query.py --steps "Data Preparation" "Data Modeling"
```
