# Full source branch

Branch: `full-source`

Do not touch `main`. Do not touch the IM repo `1004cq/cq`.

`cq.py` (52105 bytes) is stored as compressed parts:

- `src_parts/cq.b64.00` .. `src_parts/cq.b64.04`
- `restore_cq.py`

## Materialize cq.py

```bash
git clone -b full-source https://github.com/1004cq/cq-lang.git
cd cq-lang
python3 restore_cq.py
# wrote cq.py 52105
python3 cq.py run hello.cq
python3 cq.py run optimized.cq
```
