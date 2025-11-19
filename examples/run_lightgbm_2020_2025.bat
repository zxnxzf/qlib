@echo off
cd /d D:\code\qlib\qlib\examples
set SETUPTOOLS_SCM_PRETEND_VERSION_FOR_QLIB=0.9.0
qrun benchmarks/LightGBM/workflow_config_lightgbm_Alpha158_2020_2025.yaml
pause
