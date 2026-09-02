# Full source branch

Branch: `full-source`

Do not touch `main`. Do not touch the IM repo `1004cq/cq`.

`cq.py` is checked in directly and is the canonical interpreter source.

The compressed parts below are retained only as a bootstrap snapshot for older
commits that did not contain `cq.py`:

- `src_parts/cq.b64.00` .. `src_parts/cq.b64.04`
- `restore_cq.py`

## Run CQ

```bash
git clone -b full-source https://github.com/1004cq/cq-lang.git
cd cq-lang
python3 cq.py run hello.cq
python3 cq.py run optimized.cq
```

`restore_cq.py` will not overwrite an existing `cq.py`.
