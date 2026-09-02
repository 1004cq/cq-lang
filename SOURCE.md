# Full source branch

Branch: `full-source`

Complete CQ interpreter lives here. Do not touch `main`. Do not touch the IM repo `1004cq/cq`.

## Get cq.py

```bash
python3 restore_cq.py          # writes cq.py (52105 bytes)
# or
cat src_parts/cq_part_a.py src_parts/cq_part_b.py > cq.py
```

## Run

```bash
python3 cq.py run hello.cq
python3 cq.py run optimized.cq
python3 cq.py web web/app.cq web/dist
```
