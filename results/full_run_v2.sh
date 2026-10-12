#!/bin/bash
cd /data/Qswarm
python -m qswarm.simulator.experiments --full --workers 4 --out results/experiments-full > results/full-run5.log 2>&1
python -m qswarm.simulator.report --from results/experiments-full --out out >> results/full-run5.log 2>&1
echo MATRIX_AND_REPORT_DONE >> results/full-run5.log
