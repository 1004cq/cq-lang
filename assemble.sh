#!/bin/sh
set -e
cat src_parts/cq_part_a.py src_parts/cq_part_b.py > cq.py
wc -c cq.py
