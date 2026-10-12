#!/bin/bash
# M0 门禁复跑（并发 3）——复用 M0 同一命令入口 full_run_v2.sh 的形态，仅 workers 4→3。
# 记录：2026-10-10，M1 编排内核接线后的 M0 门禁复验（full 矩阵 + sim_report）。
cd /data/Qswarm || exit 1
python -m qswarm.simulator.experiments --full --workers 3 --out results/experiments-full > results/full-run6-w3.log 2>&1
python -m qswarm.simulator.report --from results/experiments-full --out out >> results/full-run6-w3.log 2>&1
echo MATRIX_AND_REPORT_DONE >> results/full-run6-w3.log
