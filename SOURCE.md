# Full source branch

Branch: `full-source`

This branch is the complete CQ 0.4 tree that was missing from `main`.

If `cq.py` arrives as parts:

```bash
cat src_parts/cq_part_a.py src_parts/cq_part_b.py > cq.py
python3 cq.py run hello.cq
python3 cq.py run optimized.cq
python3 cq.py web web/app.cq web/dist
```

Do not touch the IM repo `1004cq/cq`.
