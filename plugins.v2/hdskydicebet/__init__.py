import re
import random
import threading
from collections import Counter
from datetime import datetime, timedelta, date
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import settings
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import NotificationType
from app.utils.http import RequestUtils


class HdskyDiceBet(_PluginBase):
    """HDSky 空论坛（掷骰子）自动下注插件。"""

    plugin_name = "空论坛掷骰子下注"
    plugin_desc = "自动参与 HDSky 掷骰子论坛下注，支持固定/随机/智能策略，并汇总魔力盈亏"
    plugin_icon = "https://raw.githubusercontent.com/jxxghp/MoviePilot-Plugins/main/icons/HDSky.ico"
    plugin_version = "1.0.1"
    plugin_author = "Kuanghom"
    author_url = "https://github.com/Kuanghom"
    plugin_config_prefix = "hdskydicebet_"
    plugin_order = 25
    auth_level = 2

    LOG_TAG = "[HdskyDiceBet] "
    BASE_URL = "https://hdsky.me"
    FORUM_ID = 71
    BET_TYPES = ("豹子", "顺子", "大", "小")
    # 官方近似赔率（净利润倍数，几乎不抽水）
    ODDS = {"大": 1.29, "小": 1.29, "顺子": 7.8, "豹子": 33.0}
    # 三枚骰子古典概型（豹子 > 顺子 > 大小）
    CLASSICAL_COUNT = {"豹子": 6, "顺子": 24, "大": 93, "小": 93}
    CLASSICAL_TOTAL = 216
    TOPIC_TITLE_RE = re.compile(
        r"本轮开奖时间:\s*(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})"
        r"(?:\s*【\s*(豹子|顺子|大|小)\s+([\d,]+)\s*】)?"
    )
    BET_BODY_RE = re.compile(r"^(豹子|顺子|大|小)\s+(\d+(?:\.\d+)?[wW]?)\s*$", re.I)
    RESULT_IN_TITLE_RE = re.compile(r"【\s*(豹子|顺子|大|小)\s+")

    _enabled = False
    _notify = False
    _onlyonce = False
    _cron = "*/3 * * * *"
    _cookie = ""
    _use_proxy = True
    _bet_mode = "smart"  # fixed / random / smart
    _fixed_type = "大"
    _bet_amount = 100
    _max_daily_bets: Optional[int] = None
    _max_daily_tickets: Optional[int] = None
    _smart_history_rounds = 50
    _history_days = 90
    _username = ""
    _scheduler: Optional[BackgroundScheduler] = None
    _run_lock = threading.Lock()

    def init_plugin(self, config: dict = None):
        config = config or {}
        self._enabled = bool(config.get("enabled"))
        self._notify = bool(config.get("notify"))
        self._onlyonce = bool(config.get("onlyonce"))
        self._cron = (config.get("cron") or "*/3 * * * *").strip()
        self._cookie = (config.get("cookie") or "").strip()
        self._use_proxy = bool(config.get("use_proxy", True))
        self._bet_mode = config.get("bet_mode") or "smart"
        self._fixed_type = config.get("fixed_type") or "大"
        self._bet_amount = self._clamp_amount(config.get("bet_amount", 100))
        self._max_daily_bets = self._to_optional_int(config.get("max_daily_bets"))
        self._max_daily_tickets = self._to_optional_int(config.get("max_daily_tickets"))
        self._smart_history_rounds = max(10, int(config.get("smart_history_rounds") or 50))
        self._history_days = max(7, int(config.get("history_days") or 90))
        self._username = (self.get_data("username") or "").strip()

        self.stop_service()
        if self._onlyonce and self._enabled:
            self._onlyonce = False
            config["onlyonce"] = False
            self.update_config(config)
            self._scheduler = BackgroundScheduler(timezone=settings.TZ)
            self._scheduler.add_job(
                func=self.run_once,
                trigger="date",
                run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(seconds=3),
                name="空论坛掷骰子立即执行",
            )
            if self._scheduler.get_jobs():
                self._scheduler.start()
                logger.info(f"{self.LOG_TAG}已加入立即执行任务")

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        return []

    def get_api(self) -> List[Dict[str, Any]]:
        return []

    def get_service(self) -> List[Dict[str, Any]]:
        if not self._enabled or not self._cron:
            return []
        return [
            {
                "id": "HdskyDiceBet.Run",
                "name": "空论坛掷骰子下注",
                "trigger": CronTrigger.from_crontab(self._cron),
                "func": self.run_once,
                "kwargs": {},
            }
        ]

    def stop_service(self):
        try:
            if self._scheduler:
                self._scheduler.remove_all_jobs()
                if self._scheduler.running:
                    self._scheduler.shutdown(wait=False)
                self._scheduler = None
        except Exception as e:
            logger.error(f"{self.LOG_TAG}停止调度器失败: {e}")

    # ------------------------------------------------------------------ #
    # 配置页 / 详情页
    # ------------------------------------------------------------------ #
    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        version = getattr(settings, "VERSION_FLAG", "v1")
        cron_field = "VCronField" if version == "v2" else "VTextField"
        return [
            {
                "component": "VForm",
                "content": [
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 3},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "enabled",
                                            "label": "启用插件",
                                            "color": "primary",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 3},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "notify",
                                            "label": "开启通知",
                                            "color": "info",
                                            "hint": "下注成功/失败、开奖盈亏会推送到 MoviePilot 消息渠道",
                                            "persistent-hint": True,
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 3},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "onlyonce",
                                            "label": "立即运行一次",
                                            "color": "warning",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 3},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "use_proxy",
                                            "label": "使用代理",
                                            "color": "primary",
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VTextarea",
                                        "props": {
                                            "model": "cookie",
                                            "label": "HDSky Cookie",
                                            "rows": 2,
                                            "placeholder": "从浏览器复制 hdsky.me 的 Cookie",
                                            "hint": "需包含 c_secure_uid / c_secure_pass 等登录字段",
                                            "persistent-hint": True,
                                        },
                                    }
                                ],
                            }
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSelect",
                                        "props": {
                                            "model": "bet_mode",
                                            "label": "下注模式",
                                            "items": [
                                                {"title": "固定类型", "value": "fixed"},
                                                {"title": "随机类型", "value": "random"},
                                                {"title": "智能下注(古典概型)", "value": "smart"},
                                            ],
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSelect",
                                        "props": {
                                            "model": "fixed_type",
                                            "label": "固定下注类型",
                                            "items": [
                                                {"title": t, "value": t} for t in self.BET_TYPES
                                            ],
                                            "hint": "仅固定模式生效",
                                            "persistent-hint": True,
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "bet_amount",
                                            "label": "下注金额",
                                            "type": "number",
                                            "hint": "范围 100 ~ 100000",
                                            "persistent-hint": True,
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": cron_field,
                                        "props": {
                                            "model": "cron",
                                            "label": "执行周期",
                                            "placeholder": "*/3 * * * *",
                                            "hint": "建议每 2~5 分钟检查一轮",
                                            "persistent-hint": True,
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "max_daily_bets",
                                            "label": "每日最大下注次数",
                                            "placeholder": "不填则不限制",
                                            "hint": "按自然天统计已成功下注次数",
                                            "persistent-hint": True,
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "max_daily_tickets",
                                            "label": "每日观影券次数上限",
                                            "placeholder": "不填则不限制",
                                            "hint": "评论获得「观影随机续期奖励」按自然天累计，达上限则停止下注",
                                            "persistent-hint": True,
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "smart_history_rounds",
                                            "label": "智能策略参考历史轮数",
                                            "type": "number",
                                            "hint": "默认 50，用于古典概型偏差修正",
                                            "persistent-hint": True,
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "history_days",
                                            "label": "本地下注记录保留天数",
                                            "type": "number",
                                            "placeholder": "90",
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                ],
            }
        ], {
            "enabled": False,
            "notify": True,
            "onlyonce": False,
            "use_proxy": True,
            "cookie": "",
            "bet_mode": "smart",
            "fixed_type": "大",
            "bet_amount": 100,
            "cron": "*/3 * * * *",
            "max_daily_bets": "",
            "max_daily_tickets": "",
            "smart_history_rounds": 50,
            "history_days": 90,
        }

    def get_page(self) -> List[dict]:
        history = self.get_data("history") or []
        last_run = self.get_data("last_run") or {}
        username = self.get_data("username") or self._username or "—"
        today = self._today_str()
        day_pl = self._summarize_pl(history, "day")
        week_pl = self._summarize_pl(history, "week")
        month_pl = self._summarize_pl(history, "month")
        tickets_today = int(self.get_data("tickets_by_day") or {}).get(today, 0)
        bets_today = self._count_bets_on(history, today)

        def pl_color(v: float) -> str:
            if v > 0:
                return "success"
            if v < 0:
                return "error"
            return "secondary"

        rows = []
        for item in sorted(history, key=lambda x: x.get("time", ""), reverse=True)[:80]:
            profit = item.get("profit")
            if profit is None:
                profit_text = "待结算"
                profit_color = "warning"
            else:
                profit_text = f"{profit:+d}"
                profit_color = pl_color(profit)
            result = item.get("result") or "—"
            rows.append(
                {
                    "component": "tr",
                    "content": [
                        {"component": "td", "text": item.get("time", "—")},
                        {
                            "component": "td",
                            "content": [
                                {
                                    "component": "a",
                                    "props": {
                                        "href": item.get("url") or "#",
                                        "target": "_blank",
                                    },
                                    "text": str(item.get("topic_id", "")),
                                }
                            ],
                        },
                        {"component": "td", "text": f"{item.get('bet_type', '')} {item.get('amount', '')}"},
                        {"component": "td", "text": str(item.get("mode", ""))},
                        {"component": "td", "text": str(result)},
                        {
                            "component": "td",
                            "content": [
                                {
                                    "component": "VChip",
                                    "props": {"color": profit_color, "size": "small", "variant": "flat"},
                                    "text": profit_text,
                                }
                            ],
                        },
                        {
                            "component": "td",
                            "text": "是" if item.get("got_ticket") else "否",
                        },
                    ],
                }
            )

        return [
            {
                "component": "VRow",
                "content": [
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 3},
                        "content": [
                            {
                                "component": "VCard",
                                "props": {"variant": "tonal"},
                                "content": [
                                    {
                                        "component": "VCardText",
                                        "content": [
                                            {"component": "div", "text": f"用户：{username}"},
                                            {
                                                "component": "div",
                                                "text": f"今日下注：{bets_today}"
                                                + (
                                                    f" / {self._max_daily_bets}"
                                                    if self._max_daily_bets
                                                    else ""
                                                ),
                                            },
                                            {
                                                "component": "div",
                                                "text": f"今日观影券：{tickets_today}"
                                                + (
                                                    f" / {self._max_daily_tickets}"
                                                    if self._max_daily_tickets
                                                    else ""
                                                ),
                                            },
                                            {
                                                "component": "div",
                                                "props": {"class": "text-medium-emphasis mt-1"},
                                                "text": f"上次执行：{last_run.get('time', '—')} | {last_run.get('message', '')}",
                                            },
                                        ],
                                    }
                                ],
                            }
                        ],
                    },
                    self._summary_card("今日盈亏", day_pl, pl_color(day_pl.get("profit", 0))),
                    self._summary_card("本周盈亏", week_pl, pl_color(week_pl.get("profit", 0))),
                    self._summary_card("本月盈亏", month_pl, pl_color(month_pl.get("profit", 0))),
                ],
            },
            {
                "component": "VRow",
                "content": [
                    {
                        "component": "VCol",
                        "props": {"cols": 12},
                        "content": [
                            {
                                "component": "VCard",
                                "props": {"variant": "outlined"},
                                "content": [
                                    {"component": "VCardTitle", "text": "下注记录"},
                                    {
                                        "component": "VCardText",
                                        "content": [
                                            {
                                                "component": "VTable",
                                                "props": {"hover": True, "density": "compact"},
                                                "content": [
                                                    {
                                                        "component": "thead",
                                                        "content": [
                                                            {
                                                                "component": "tr",
                                                                "content": [
                                                                    {"component": "th", "text": "时间"},
                                                                    {"component": "th", "text": "帖子"},
                                                                    {"component": "th", "text": "下注"},
                                                                    {"component": "th", "text": "模式"},
                                                                    {"component": "th", "text": "开奖"},
                                                                    {"component": "th", "text": "盈亏"},
                                                                    {"component": "th", "text": "观影券"},
                                                                ],
                                                            }
                                                        ],
                                                    },
                                                    {
                                                        "component": "tbody",
                                                        "content": rows
                                                        or [
                                                            {
                                                                "component": "tr",
                                                                "content": [
                                                                    {
                                                                        "component": "td",
                                                                        "props": {"colspan": 7},
                                                                        "text": "暂无下注记录",
                                                                    }
                                                                ],
                                                            }
                                                        ],
                                                    },
                                                ],
                                            }
                                        ],
                                    },
                                ],
                            }
                        ],
                    }
                ],
            },
        ]

    @staticmethod
    def _summary_card(title: str, summary: Dict[str, Any], color: str) -> dict:
        profit = summary.get("profit", 0)
        return {
            "component": "VCol",
            "props": {"cols": 12, "md": 3},
            "content": [
                {
                    "component": "VCard",
                    "props": {"variant": "tonal", "color": color},
                    "content": [
                        {
                            "component": "VCardText",
                            "content": [
                                {"component": "div", "props": {"class": "text-subtitle-2"}, "text": title},
                                {
                                    "component": "div",
                                    "props": {"class": "text-h5"},
                                    "text": f"{profit:+d}",
                                },
                                {
                                    "component": "div",
                                    "props": {"class": "text-caption"},
                                    "text": f"下注 {summary.get('bets', 0)} 次 | "
                                    f"已结算 {summary.get('settled', 0)} | "
                                    f"胜 {summary.get('wins', 0)} 负 {summary.get('losses', 0)}",
                                },
                            ],
                        }
                    ],
                }
            ],
        }

    # ------------------------------------------------------------------ #
    # 通知（风格对齐蜂巢签到）
    # ------------------------------------------------------------------ #
    def _send_notification(self, title: str, text: str):
        if not self._notify:
            return
        self.post_message(
            mtype=NotificationType.SiteMessage,
            title=title,
            text=text,
        )

    def _notify_bet_success(self, record: Dict[str, Any]):
        today = self._today_str()
        history = self.get_data("history") or []
        day_pl = self._summarize_pl(history, "day")
        bets_today = self._count_bets_on(history, today)
        tickets_today = int((self.get_data("tickets_by_day") or {}).get(today, 0))
        mode_map = {"fixed": "固定", "random": "随机", "smart": "智能", "manual": "手动"}
        mode_text = mode_map.get(str(record.get("mode")), str(record.get("mode")))
        limit_bet = f" / {self._max_daily_bets}" if self._max_daily_bets else ""
        limit_ticket = f" / {self._max_daily_tickets}" if self._max_daily_tickets else ""
        self._send_notification(
            title="【✅ 空论坛下注成功】",
            text=(
                f"📢 执行结果\n"
                f"━━━━━━━━━━\n"
                f"🕐 时间：{record.get('time') or self._now_str()}\n"
                f"✨ 状态：下注成功\n"
                f"🎲 类型：{record.get('bet_type')} {record.get('amount')}\n"
                f"🧠 模式：{mode_text}\n"
                f"📌 帖子：#{record.get('topic_id')}\n"
                f"⏳ 开奖：{record.get('draw_time') or '—'}\n"
                f"━━━━━━━━━━\n"
                f"📊 今日统计\n"
                f"🧾 下注：{bets_today}{limit_bet} 次\n"
                f"🎫 观影券：{tickets_today}{limit_ticket}\n"
                f"💰 今日盈亏：{day_pl.get('profit', 0):+d}\n"
                f"━━━━━━━━━━"
            ),
        )

    def _notify_bet_failure(self, topic_id: str, reason: str):
        self._send_notification(
            title="【❌ 空论坛下注失败】",
            text=(
                f"📢 执行结果\n"
                f"━━━━━━━━━━\n"
                f"🕐 时间：{self._now_str()}\n"
                f"❌ 状态：下注失败\n"
                f"📌 帖子：#{topic_id}\n"
                f"💬 原因：{reason}\n"
                f"━━━━━━━━━━"
            ),
        )

    def _notify_settlement(self, item: Dict[str, Any]):
        profit = int(item.get("profit") or 0)
        won = profit > 0
        title = "【🎉 空论坛开奖盈利】" if won else (
            "【💔 空论坛开奖亏损】" if profit < 0 else "【ℹ️ 空论坛已开奖】"
        )
        status = "猜中盈利" if won else ("未中亏损" if profit < 0 else "已结算")
        day_pl = self._summarize_pl(self.get_data("history") or [], "day")
        week_pl = self._summarize_pl(self.get_data("history") or [], "week")
        self._send_notification(
            title=title,
            text=(
                f"📢 执行结果\n"
                f"━━━━━━━━━━\n"
                f"🕐 时间：{self._now_str()}\n"
                f"✨ 状态：{status}\n"
                f"🎲 下注：{item.get('bet_type')} {item.get('amount')}\n"
                f"🏆 开奖：{item.get('result') or '—'}\n"
                f"💵 本局盈亏：{profit:+d}\n"
                f"📌 帖子：#{item.get('topic_id')}\n"
                f"━━━━━━━━━━\n"
                f"📊 汇总\n"
                f"📅 今日盈亏：{day_pl.get('profit', 0):+d}\n"
                f"🗓️ 本周盈亏：{week_pl.get('profit', 0):+d}\n"
                f"🎫 观影券：{'是' if item.get('got_ticket') else '否'}\n"
                f"━━━━━━━━━━"
            ),
        )

    # ------------------------------------------------------------------ #
    # 主流程
    # ------------------------------------------------------------------ #
    def run_once(self):
        if not self._run_lock.acquire(blocking=False):
            logger.warning(f"{self.LOG_TAG}上一次任务仍在执行，跳过")
            return
        try:
            message = self._run_internal()
            self.save_data(
                "last_run",
                {"time": self._now_str(), "message": message},
            )
            logger.info(f"{self.LOG_TAG}{message}")
        except Exception as e:
            logger.error(f"{self.LOG_TAG}执行异常: {e}", exc_info=True)
            self.save_data(
                "last_run",
                {"time": self._now_str(), "message": f"异常: {e}"},
            )
            self._send_notification(
                title="【❌ 空论坛下注异常】",
                text=(
                    f"📢 执行结果\n"
                    f"━━━━━━━━━━\n"
                    f"🕐 时间：{self._now_str()}\n"
                    f"❌ 状态：执行异常\n"
                    f"💬 原因：{e}\n"
                    f"━━━━━━━━━━"
                ),
            )
        finally:
            self._run_lock.release()

    def _run_internal(self) -> str:
        if not self._cookie:
            return "未配置 Cookie"
        if not self._ensure_username():
            return "Cookie 无效或无法识别用户名"

        # 先同步未结算记录与观影券
        self._sync_pending_results()
        self._refresh_today_tickets()

        today = self._today_str()
        history = self.get_data("history") or []
        bets_today = self._count_bets_on(history, today)
        tickets_today = int((self.get_data("tickets_by_day") or {}).get(today, 0))

        if self._max_daily_bets is not None and bets_today >= self._max_daily_bets:
            return f"已达每日下注上限 {self._max_daily_bets}"
        if self._max_daily_tickets is not None and tickets_today >= self._max_daily_tickets:
            return f"已达每日观影券上限 {self._max_daily_tickets}（今日 {tickets_today}）"

        topics = self._list_forum_topics(pages=2)
        open_topics = [t for t in topics if t.get("open")]
        if not open_topics:
            return "当前没有可下注帖子"

        # 优先最早开奖的开放帖
        open_topics.sort(key=lambda x: x.get("draw_time") or "")
        acted = []
        for topic in open_topics:
            if self._max_daily_bets is not None and self._count_bets_on(
                self.get_data("history") or [], today
            ) >= self._max_daily_bets:
                break
            if self._already_bet_topic(topic["topic_id"]):
                continue
            # 二次确认帖内是否已下注 / 是否已锁定
            detail = self._fetch_topic_detail(topic["topic_id"])
            if not detail:
                continue
            if detail.get("locked") or detail.get("result"):
                continue
            if detail.get("self_bet"):
                self._remember_existing_bet(topic, detail["self_bet"])
                continue
            bet_type = self._choose_bet_type(topics)
            amount = self._clamp_amount(self._bet_amount)
            ok, msg = self._post_bet(topic["topic_id"], bet_type, amount)
            if ok:
                record = {
                    "time": self._now_str(),
                    "date": today,
                    "topic_id": topic["topic_id"],
                    "draw_time": topic.get("draw_time"),
                    "url": f"{self.BASE_URL}/forums.php?action=viewtopic&forumid={self.FORUM_ID}&topicid={topic['topic_id']}",
                    "bet_type": bet_type,
                    "amount": amount,
                    "mode": self._bet_mode,
                    "result": None,
                    "profit": None,
                    "got_ticket": False,
                    "status": "pending",
                }
                self._append_history(record)
                acted.append(f"{bet_type} {amount} @#{topic['topic_id']}")
                self._notify_bet_success(record)
            else:
                acted.append(f"失败#{topic['topic_id']}:{msg}")
                logger.warning(f"{self.LOG_TAG}下注失败 topic={topic['topic_id']}: {msg}")
                self._notify_bet_failure(topic["topic_id"], msg)

        if not acted:
            return "有开放帖，但均已下注或不可投"
        return "；".join(acted)

    # ------------------------------------------------------------------ #
    # HTTP / 解析
    # ------------------------------------------------------------------ #
    def _proxies(self) -> Optional[dict]:
        if not self._use_proxy:
            return None
        return settings.PROXY

    def _get(self, path: str) -> Optional[str]:
        url = path if path.startswith("http") else urljoin(self.BASE_URL + "/", path.lstrip("/"))
        res = RequestUtils(
            cookies=self._cookie,
            proxies=self._proxies(),
            timeout=30,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Referer": f"{self.BASE_URL}/forums.php?action=viewforum&forumid={self.FORUM_ID}",
            },
        ).get_res(url=url)
        if not res or res.status_code != 200:
            logger.warning(f"{self.LOG_TAG}GET 失败 {url}: {getattr(res, 'status_code', None)}")
            return None
        text = res.text or ""
        if "该页面必须在登录后才能访问" in text or "<title>HDSky :: 登录" in text:
            logger.error(f"{self.LOG_TAG}Cookie 已失效")
            return None
        return text

    def _post(self, path: str, data: dict) -> Optional[str]:
        url = path if path.startswith("http") else urljoin(self.BASE_URL + "/", path.lstrip("/"))
        res = RequestUtils(
            cookies=self._cookie,
            proxies=self._proxies(),
            timeout=30,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Referer": f"{self.BASE_URL}/forums.php?action=viewforum&forumid={self.FORUM_ID}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        ).post_res(url=url, data=data)
        if not res:
            return None
        return res.text or ""

    def _ensure_username(self) -> bool:
        html = self._get(f"/forums.php?action=viewforum&forumid={self.FORUM_ID}")
        if not html:
            return False
        m = re.search(
            r"欢迎回来\s*,\s*<span[^>]*>\s*<a[^>]*userdetails\.php\?id=(\d+)[^>]*>\s*<b>([^<]+)</b>",
            html,
        )
        if not m:
            m = re.search(r"userdetails\.php\?id=(\d+)[^>]*>\s*<b>([^<]+)</b>", html)
        if not m:
            return False
        self._username = m.group(2).strip()
        self.save_data("username", self._username)
        self.save_data("uid", m.group(1))
        return True

    def _list_forum_topics(self, pages: int = 2) -> List[Dict[str, Any]]:
        topics: List[Dict[str, Any]] = []
        seen = set()
        for page in range(pages):
            html = self._get(
                f"/forums.php?action=viewforum&forumid={self.FORUM_ID}&page={page}"
            )
            if not html:
                break
            for topic in self._parse_forum_list(html):
                if topic["topic_id"] in seen:
                    continue
                seen.add(topic["topic_id"])
                topics.append(topic)
        return topics

    def _parse_forum_list(self, html: str) -> List[Dict[str, Any]]:
        results = []
        # 每行主题大致在 <tr>...topicid=...本轮开奖...
        row_re = re.compile(
            r'<tr>\s*<td class="rowfollow"[^>]*>.*?'
            r'(?:class="(locked|lockednew|unlocked|unlockednew)"[^>]*>).*?'
            r'href="[^"]*topicid=(\d+)[^"]*"\s*>(.*?)</a>',
            re.S | re.I,
        )
        for m in row_re.finditer(html):
            lock_cls, topic_id, title_html = m.group(1), m.group(2), m.group(3)
            title = re.sub(r"<[^>]+>", "", title_html)
            title = re.sub(r"\s+", " ", title).strip()
            tm = self.TOPIC_TITLE_RE.search(title)
            if not tm:
                continue
            draw_time, result_type, dice = tm.group(1), tm.group(2), tm.group(3)
            locked = lock_cls.startswith("locked") or bool(result_type) or ("锁定" in title)
            openable = (not locked) and (not result_type) and self._is_before_draw(draw_time)
            results.append(
                {
                    "topic_id": topic_id,
                    "title": title,
                    "draw_time": draw_time,
                    "result": result_type,
                    "dice": dice,
                    "locked": locked,
                    "open": openable,
                }
            )
        return results

    def _fetch_topic_detail(self, topic_id: str, max_pages: int = 5) -> Optional[Dict[str, Any]]:
        first = self._get(
            f"/forums.php?action=viewtopic&forumid={self.FORUM_ID}&topicid={topic_id}"
        )
        if not first:
            return None
        title_m = re.search(r'<span id="top">(.*?)</span>', first, re.S)
        title = re.sub(r"<[^>]+>", "", title_m.group(1) if title_m else "")
        title = re.sub(r"\s+", " ", title).strip()
        tm = self.TOPIC_TITLE_RE.search(title)
        result = tm.group(2) if tm else None
        locked = ("锁定" in title) or bool(result) or ("compose" not in first)
        pages = self._topic_page_count(first)
        pages = min(pages, max_pages)
        html_all = first
        for page in range(1, pages):
            more = self._get(
                f"/forums.php?action=viewtopic&forumid={self.FORUM_ID}&topicid={topic_id}&page={page}"
            )
            if more:
                html_all += more

        self_bet = None
        got_ticket = False
        profit = None
        if self._username:
            for post in self._parse_posts(html_all):
                if post["username"] != self._username:
                    continue
                if post.get("bet_type"):
                    # 取该用户在本帖的下注合计盈亏
                    self_bet = {
                        "bet_type": post["bet_type"],
                        "amount": post["amount"],
                        "profit": post.get("settle_profit"),
                        "got_ticket": post.get("got_ticket", False),
                    }
                if post.get("got_ticket"):
                    got_ticket = True
                if post.get("settle_profit") is not None:
                    profit = (profit or 0) + post["settle_profit"]
                    if self_bet:
                        self_bet["profit"] = profit
                        self_bet["got_ticket"] = got_ticket or self_bet.get("got_ticket")

        return {
            "topic_id": topic_id,
            "title": title,
            "result": result,
            "locked": locked,
            "self_bet": self_bet,
            "got_ticket": got_ticket,
            "profit": profit,
        }

    @staticmethod
    def _topic_page_count(html: str) -> int:
        pages = {0}
        for m in re.finditer(r"[?&]page=(\d+)", html):
            pages.add(int(m.group(1)))
        # page 链接是 0-based；最大 page+1 为页数
        return max(pages) + 1

    def _parse_posts(self, html: str) -> List[Dict[str, Any]]:
        posts = []
        # 以 pidXXXX 表头切分
        parts = re.split(r'<table id="(pid\d+)"', html)
        # parts: [before, id1, chunk1, id2, chunk2, ...]
        for i in range(1, len(parts), 2):
            pid = parts[i]
            chunk = parts[i + 1] if i + 1 < len(parts) else ""
            user_m = re.search(
                r"userdetails\.php\?id=\d+[^>]*>\s*<b>([^<]+)</b>",
                chunk,
            )
            body_m = re.search(rf'id="{pid}body">(.*?)</div>', chunk, re.S)
            if not user_m or not body_m:
                continue
            username = user_m.group(1).strip()
            body_html = body_m.group(1)
            body_text = re.sub(r"<br\s*/?>", "\n", body_html)
            body_text = re.sub(r"<[^>]+>", "", body_text)
            first_line = next((ln.strip() for ln in body_text.splitlines() if ln.strip()), "")
            bet_type, amount = None, None
            bm = self.BET_BODY_RE.match(first_line)
            if bm:
                bet_type = bm.group(1)
                amount = self._parse_amount(bm.group(2))

            # 评分在 body 后的橙色块；理由需精确匹配，避免多条评分粘连误判
            rating_block = ""
            rb = re.search(r"\[评分\](.*?)</div>", chunk, re.S)
            if rb:
                rating_block = re.sub(r"<br\s*/?>", "\n", rb.group(1), flags=re.I)
                rating_block = re.sub(r"<[^>]+>", " ", rating_block)
            settle_profit = None
            got_ticket = False
            for rm in re.finditer(
                r"([+-]\d[\d,]*)\s*评分理由\s*[:：]\s*(观影随机续期奖励|兑奖)",
                rating_block,
            ):
                value = int(rm.group(1).replace(",", ""))
                reason = rm.group(2).strip()
                if reason == "观影随机续期奖励":
                    got_ticket = True
                elif reason == "兑奖":
                    settle_profit = (settle_profit or 0) + value

            posts.append(
                {
                    "pid": pid,
                    "username": username,
                    "bet_type": bet_type,
                    "amount": amount,
                    "settle_profit": settle_profit,
                    "got_ticket": got_ticket,
                }
            )
        return posts

    def _post_bet(self, topic_id: str, bet_type: str, amount: int) -> Tuple[bool, str]:
        body = f"{bet_type} {amount}"
        html = self._post(
            "/forums.php?action=post",
            data={"id": topic_id, "type": "reply", "body": body},
        )
        if html is None:
            return False, "请求失败"
        if "该页面必须在登录后才能访问" in html:
            return False, "Cookie 失效"
        # 成功后通常会跳回主题；再读一次确认
        detail = self._fetch_topic_detail(topic_id, max_pages=2)
        if detail and detail.get("self_bet"):
            return True, "ok"
        # 有些站点 post 后直接带上自己的回复
        if self._username and self._username in (html or "") and bet_type in (html or ""):
            return True, "ok"
        if "错误" in (html or "") and "登录" not in (html or ""):
            err = re.search(r"<h1[^>]*>错误[:：]?(.*?)</h1>", html or "", re.S)
            return False, re.sub(r"<[^>]+>", "", err.group(1)).strip() if err else "发帖错误"
        # 宽松成功：没有明显错误页
        if html and "compose" in html and body not in html:
            # 仍在发帖页，可能失败
            return False, "仍停留在发帖页"
        return True, "ok"

    # ------------------------------------------------------------------ #
    # 智能下注（古典概型 + 历史偏差）
    # ------------------------------------------------------------------ #
    def _choose_bet_type(self, recent_topics: List[Dict[str, Any]]) -> str:
        mode = (self._bet_mode or "smart").lower()
        if mode == "fixed":
            t = self._fixed_type if self._fixed_type in self.BET_TYPES else "大"
            return t
        if mode == "random":
            return random.choice(list(self.BET_TYPES))
        return self._smart_choose(recent_topics)

    def _smart_choose(self, recent_topics: List[Dict[str, Any]]) -> str:
        """
        古典概型智能策略：
        1. 理论概率 P_theo（三骰子 216 种等可能结果，优先级 豹子>顺子>大小）
        2. 取最近 N 轮已开奖结果得经验频率 P_emp
        3. 对每类计算「短缺分」deficit = P_theo - P_emp（越大说明越该回补）
        4. 结合理论期望 EV = P_theo * odds - (1 - P_theo) 做轻微加权
        5. 选综合得分最高者；大小并列时看最近连续未出侧
        """
        # 按帖子去重收集最近已开奖结果
        result_pairs = []
        seen_topics = set()
        for t in recent_topics:
            tid, res = t.get("topic_id"), t.get("result")
            if not res or not tid or tid in seen_topics:
                continue
            seen_topics.add(tid)
            result_pairs.append(res)
        if len(result_pairs) < self._smart_history_rounds:
            for t in self._list_forum_topics(pages=5):
                tid, res = t.get("topic_id"), t.get("result")
                if not res or not tid or tid in seen_topics:
                    continue
                seen_topics.add(tid)
                result_pairs.append(res)
                if len(result_pairs) >= self._smart_history_rounds:
                    break
        results = result_pairs[: self._smart_history_rounds]
        n = len(results) or 1
        emp = Counter(results)
        scores = {}
        for t in self.BET_TYPES:
            p_theo = self.CLASSICAL_COUNT[t] / self.CLASSICAL_TOTAL
            p_emp = emp.get(t, 0) / n
            deficit = p_theo - p_emp
            odds = self.ODDS[t]
            ev = p_theo * odds - (1 - p_theo)
            # 短缺为主，期望为辅（大小 EV 略优，避免长期偏豹子）
            scores[t] = deficit * 1.0 + ev * 0.15

        # 最近连续未出现加权（冷号回补）
        for t in self.BET_TYPES:
            streak = 0
            for r in results:
                if r == t:
                    break
                streak += 1
            scores[t] += streak * (self.CLASSICAL_COUNT[t] / self.CLASSICAL_TOTAL) * 0.02

        best = max(scores, key=scores.get)
        # 若最佳是大/小且分差极小，选更冷的一侧
        if best in ("大", "小"):
            other = "小" if best == "大" else "大"
            if abs(scores[best] - scores[other]) < 0.01:
                best = min(("大", "小"), key=lambda x: emp.get(x, 0))
        logger.info(
            f"{self.LOG_TAG}智能选择={best} scores={{{', '.join(f'{k}:{v:.4f}' for k,v in scores.items())}}} "
            f"样本={len(results)} emp={dict(emp)}"
        )
        return best

    # ------------------------------------------------------------------ #
    # 记录 / 同步 / 汇总
    # ------------------------------------------------------------------ #
    def _already_bet_topic(self, topic_id: str) -> bool:
        history = self.get_data("history") or []
        return any(str(h.get("topic_id")) == str(topic_id) for h in history)

    def _remember_existing_bet(self, topic: Dict[str, Any], self_bet: Dict[str, Any]):
        if self._already_bet_topic(topic["topic_id"]):
            return
        record = {
            "time": self._now_str(),
            "date": self._today_str(),
            "topic_id": topic["topic_id"],
            "draw_time": topic.get("draw_time"),
            "url": f"{self.BASE_URL}/forums.php?action=viewtopic&forumid={self.FORUM_ID}&topicid={topic['topic_id']}",
            "bet_type": self_bet.get("bet_type"),
            "amount": self_bet.get("amount"),
            "mode": "manual",
            "result": topic.get("result"),
            "profit": self_bet.get("profit"),
            "got_ticket": bool(self_bet.get("got_ticket")),
            "status": "settled" if self_bet.get("profit") is not None else "pending",
        }
        self._append_history(record)

    def _append_history(self, record: Dict[str, Any]):
        history = self.get_data("history") or []
        history.append(record)
        # 清理过期
        cutoff = (datetime.now() - timedelta(days=self._history_days)).strftime("%Y-%m-%d")
        history = [h for h in history if (h.get("date") or "") >= cutoff]
        self.save_data("history", history)

    def _sync_pending_results(self):
        history = self.get_data("history") or []
        changed = False
        tickets_by_day = dict(self.get_data("tickets_by_day") or {})
        newly_settled: List[Dict[str, Any]] = []
        for item in history:
            if item.get("status") == "settled" and item.get("profit") is not None:
                continue
            topic_id = item.get("topic_id")
            if not topic_id:
                continue
            was_pending = item.get("profit") is None
            detail = self._fetch_topic_detail(str(topic_id), max_pages=3)
            if not detail:
                continue
            if detail.get("result"):
                item["result"] = detail["result"]
            if detail.get("self_bet") and detail["self_bet"].get("profit") is not None:
                item["profit"] = detail["self_bet"]["profit"]
                item["status"] = "settled"
                changed = True
            elif detail.get("profit") is not None:
                item["profit"] = detail["profit"]
                item["status"] = "settled"
                changed = True
            if detail.get("got_ticket") or (
                detail.get("self_bet") and detail["self_bet"].get("got_ticket")
            ):
                if not item.get("got_ticket"):
                    item["got_ticket"] = True
                    day = item.get("date") or self._today_str()
                    tickets_by_day[day] = int(tickets_by_day.get(day, 0)) + 1
                    changed = True
            # 标题已开奖但尚未扫到自己评分时，用理论盈亏兜底
            if detail.get("locked") and detail.get("result") and item.get("profit") is None:
                if detail.get("self_bet") and detail["self_bet"].get("bet_type"):
                    bet_type = detail["self_bet"]["bet_type"]
                    amount = int(detail["self_bet"].get("amount") or item.get("amount") or 0)
                    result = detail["result"]
                    if bet_type == result:
                        item["profit"] = int(round(amount * self.ODDS.get(bet_type, 0)))
                    else:
                        item["profit"] = -amount
                    item["status"] = "settled"
                    item["result"] = result
                    changed = True
            if was_pending and item.get("profit") is not None:
                newly_settled.append(dict(item))
        if changed:
            self.save_data("history", history)
            self.save_data("tickets_by_day", tickets_by_day)
        for item in newly_settled:
            self._notify_settlement(item)

    def _refresh_today_tickets(self):
        """按自然天统计自己评论中的观影券次数。"""
        today = self._today_str()
        history = self.get_data("history") or []
        known_ids = {str(h.get("topic_id")) for h in history if h.get("got_ticket")}
        count = sum(1 for h in history if h.get("date") == today and h.get("got_ticket"))

        # 仅在配置了观影券上限时额外扫今日已结帖，补录非本插件下注获得的券
        if self._max_daily_tickets is not None:
            topics = self._list_forum_topics(pages=2)
            checked = 0
            for t in topics:
                if not (t.get("draw_time") or "").startswith(today):
                    continue
                if not t.get("result") and not t.get("locked"):
                    continue
                tid = str(t["topic_id"])
                if tid in known_ids:
                    continue
                checked += 1
                if checked > 12:
                    break
                detail = self._fetch_topic_detail(tid, max_pages=2)
                if detail and detail.get("got_ticket"):
                    count += 1
                    known_ids.add(tid)

        tickets_by_day = dict(self.get_data("tickets_by_day") or {})
        tickets_by_day[today] = count
        cutoff = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")
        tickets_by_day = {k: v for k, v in tickets_by_day.items() if k >= cutoff}
        self.save_data("tickets_by_day", tickets_by_day)

    def _summarize_pl(self, history: List[dict], period: str) -> Dict[str, Any]:
        today = date.today()
        if period == "day":
            start = today
        elif period == "week":
            start = today - timedelta(days=today.weekday())
        else:
            start = today.replace(day=1)
        start_s = start.strftime("%Y-%m-%d")
        bets = 0
        settled = 0
        wins = 0
        losses = 0
        profit = 0
        for h in history:
            d = h.get("date") or ""
            if d < start_s:
                continue
            bets += 1
            if h.get("profit") is None:
                continue
            settled += 1
            p = int(h["profit"])
            profit += p
            if p > 0:
                wins += 1
            elif p < 0:
                losses += 1
        return {
            "bets": bets,
            "settled": settled,
            "wins": wins,
            "losses": losses,
            "profit": profit,
        }

    @staticmethod
    def _count_bets_on(history: List[dict], day: str) -> int:
        return sum(1 for h in history if h.get("date") == day)

    # ------------------------------------------------------------------ #
    # 工具
    # ------------------------------------------------------------------ #
    @staticmethod
    def _clamp_amount(value: Any) -> int:
        try:
            amount = int(float(value))
        except (TypeError, ValueError):
            amount = 100
        return max(100, min(100000, amount))

    @staticmethod
    def _to_optional_int(value: Any) -> Optional[int]:
        if value is None or value == "":
            return None
        try:
            n = int(value)
            return n if n > 0 else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _parse_amount(text: str) -> int:
        text = (text or "").strip().lower().replace(",", "")
        if text.endswith("w"):
            return int(float(text[:-1]) * 10000)
        return int(float(text))

    def _is_before_draw(self, draw_time: str) -> bool:
        try:
            tz = pytz.timezone(settings.TZ)
            dt = tz.localize(datetime.strptime(draw_time, "%Y-%m-%d %H:%M:%S"))
            return datetime.now(tz) < dt
        except Exception:
            return True

    def _now_str(self) -> str:
        return datetime.now(tz=pytz.timezone(settings.TZ)).strftime("%Y-%m-%d %H:%M:%S")

    def _today_str(self) -> str:
        return datetime.now(tz=pytz.timezone(settings.TZ)).strftime("%Y-%m-%d")
