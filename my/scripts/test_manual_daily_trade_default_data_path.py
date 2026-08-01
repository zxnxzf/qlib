import importlib.util
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "examples" / "custom" / "manual_daily_trade.py"


def _load_manual_daily_trade():
    spec = importlib.util.spec_from_file_location("manual_daily_trade_for_test", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_manual_daily_trade_uses_project_local_data_by_default():
    module = _load_manual_daily_trade()
    expected = str(Path(__file__).resolve().parents[1] / "data" / "cn_data")

    assert module.LOCAL_PROVIDER_URI == expected
    assert module.DEFAULT_CONFIG["qlib_init"]["provider_uri"] == expected
    assert module.DEFAULT_CONFIG["prediction"]["provider_uri"] == expected
    assert module.PredictionConfig.__dataclass_fields__["provider_uri"].default == expected
