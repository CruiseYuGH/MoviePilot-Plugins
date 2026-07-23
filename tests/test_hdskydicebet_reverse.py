import importlib.util
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_FILE = ROOT / "plugins.v2" / "hdskydicebet" / "__init__.py"


class _BasePluginStub:
    def __init__(self):
        self._data = {}

    def get_data(self, key):
        return self._data.get(key)

    def save_data(self, key, value):
        self._data[key] = value

    def update_config(self, config):
        self._config = config

    def post_message(self, *args, **kwargs):
        return None


class _LoggerStub:
    def debug(self, *args, **kwargs):
        return None

    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None

    def error(self, *args, **kwargs):
        return None


def _install_stubs():
    app = types.ModuleType("app")
    app_core = types.ModuleType("app.core")
    app_core_config = types.ModuleType("app.core.config")
    app_core_config.settings = types.SimpleNamespace(TZ="Asia/Shanghai")
    app_db = types.ModuleType("app.db")
    app_db_site_oper = types.ModuleType("app.db.site_oper")
    app_db_site_oper.SiteOper = object
    app_helper = types.ModuleType("app.helper")
    app_helper_sites = types.ModuleType("app.helper.sites")
    app_helper_sites.SitesHelper = object
    app_log = types.ModuleType("app.log")
    app_log.logger = _LoggerStub()
    app_plugins = types.ModuleType("app.plugins")
    app_plugins._PluginBase = _BasePluginStub
    app_schemas = types.ModuleType("app.schemas")
    app_schemas.NotificationType = types.SimpleNamespace(SiteMessage="SiteMessage")
    app_utils = types.ModuleType("app.utils")
    app_utils_http = types.ModuleType("app.utils.http")
    app_utils_http.RequestUtils = object

    apscheduler = types.ModuleType("apscheduler")
    apscheduler_schedulers = types.ModuleType("apscheduler.schedulers")
    apscheduler_background = types.ModuleType("apscheduler.schedulers.background")
    apscheduler_background.BackgroundScheduler = object
    apscheduler_triggers = types.ModuleType("apscheduler.triggers")
    apscheduler_cron = types.ModuleType("apscheduler.triggers.cron")
    apscheduler_cron.CronTrigger = object

    pytz = types.ModuleType("pytz")
    pytz.timezone = lambda name: None

    modules = {
        "app": app,
        "app.core": app_core,
        "app.core.config": app_core_config,
        "app.db": app_db,
        "app.db.site_oper": app_db_site_oper,
        "app.helper": app_helper,
        "app.helper.sites": app_helper_sites,
        "app.log": app_log,
        "app.plugins": app_plugins,
        "app.schemas": app_schemas,
        "app.utils": app_utils,
        "app.utils.http": app_utils_http,
        "apscheduler": apscheduler,
        "apscheduler.schedulers": apscheduler_schedulers,
        "apscheduler.schedulers.background": apscheduler_background,
        "apscheduler.triggers": apscheduler_triggers,
        "apscheduler.triggers.cron": apscheduler_cron,
        "pytz": pytz,
    }
    sys.modules.update(modules)


def _load_plugin_class():
    _install_stubs()
    spec = importlib.util.spec_from_file_location("hdskydicebet_plugin", PLUGIN_FILE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module.HdskyDiceBet


class HdskyDiceBetReverseModeTest(unittest.TestCase):
    def setUp(self):
        plugin_class = _load_plugin_class()
        self.plugin = plugin_class()
        self.plugin._bet_mode = "reverse"
        self.plugin._bet_amount = 100
        self.plugin._amount_by_type = {}
        self.plugin._reverse_trigger_streak = 5
        self.plugin._reverse_max_chase = 8
        self.plugin._smart_history_rounds = 6

    def _topics(self, results):
        return [
            {"topic_id": str(7000 - idx), "result": result}
            for idx, result in enumerate(results)
        ]

    def test_reverse_mode_bets_opposite_side_after_configured_streak(self):
        plans = self.plugin._resolve_bet_plans(
            self._topics(["大", "大", "大", "大", "大", "小"])
        )

        self.assertEqual(plans, [("小", 100)])

    def test_reverse_mode_continues_same_side_after_losing_chase(self):
        self.plugin._data["history"] = [
            {
                "time": "2026-07-23 10:00:00",
                "topic_id": "6999",
                "bet_type": "小",
                "mode": "reverse",
                "result": "大",
                "profit": -100,
                "status": "settled",
            }
        ]

        plans = self.plugin._resolve_bet_plans(
            self._topics(["大", "大", "大", "大", "大", "小"])
        )

        self.assertEqual(plans, [("小", 100)])

    def test_reverse_mode_waits_when_latest_reverse_bet_is_pending(self):
        self.plugin._data["history"] = [
            {
                "time": "2026-07-23 10:00:00",
                "topic_id": "6999",
                "bet_type": "小",
                "mode": "reverse",
                "result": None,
                "profit": None,
                "status": "pending",
            }
        ]

        plans = self.plugin._resolve_bet_plans(
            self._topics(["大", "大", "大", "大", "大", "小"])
        )

        self.assertEqual(plans, [])

    def test_reverse_mode_stops_after_configured_max_chase_losses(self):
        self.plugin._data["history"] = [
            {
                "time": f"2026-07-23 10:0{idx}:00",
                "topic_id": str(6999 - idx),
                "bet_type": "小",
                "mode": "reverse",
                "result": "大",
                "profit": -100,
                "status": "settled",
            }
            for idx in range(8)
        ]

        plans = self.plugin._resolve_bet_plans(
            self._topics(["大", "大", "大", "大", "大", "小"])
        )

        self.assertEqual(plans, [])


if __name__ == "__main__":
    unittest.main()
