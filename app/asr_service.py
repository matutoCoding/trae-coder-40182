from abc import ABC, abstractmethod
import asyncio
import hashlib
import logging
import re
from typing import List, Optional
from urllib.parse import urlparse

from .schemas import Speaker, TranscriptSegment
from .config import settings

logger = logging.getLogger(__name__)


MOCK_TRANSCRIPTS_NORMAL = [
    (Speaker.AGENT, "您好，这里是XX客服中心，本次通话将被录音，请问有什么可以帮您？"),
    (Speaker.CUSTOMER, "你好，我想咨询一下你们最近推出的理财产品。"),
    (Speaker.AGENT, "好的女士，请问您怎么称呼？方便留一下联系方式吗？"),
    (Speaker.CUSTOMER, "我姓张，手机号是138****5678。"),
    (Speaker.AGENT, "好的张女士，我给您简单介绍一下我们的稳健型理财产品，这款产品预期年化收益率在4.5%左右，风险较低。"),
    (Speaker.CUSTOMER, "听起来不错，那这个产品的期限是多久？"),
    (Speaker.AGENT, "期限有3个月、6个月和12个月三种可选，您可以根据自己的资金安排来选择。"),
    (Speaker.CUSTOMER, "那我再考虑一下吧，有需要再联系你们。"),
    (Speaker.AGENT, "好的张女士，感谢您的来电，祝您生活愉快，再见。"),
    (Speaker.CUSTOMER, "再见。"),
]

MOCK_TRANSCRIPTS_WECHAT = [
    (Speaker.AGENT, "您好，这里是XX客服中心，请问有什么可以帮您？"),
    (Speaker.CUSTOMER, "你好，我之前办的信用卡想查一下账单。"),
    (Speaker.AGENT, "好的先生，请问您的卡号是多少？"),
    (Speaker.CUSTOMER, "卡号我记不太清了，身份证号可以吗？"),
    (Speaker.AGENT, "可以的，请您提供一下身份证号。"),
    (Speaker.CUSTOMER, "110***********1234"),
    (Speaker.AGENT, "查到了王先生，您本月账单金额是3580元，最后还款日是下个月5号。"),
    (Speaker.CUSTOMER, "好的，我还有个问题想咨询一下，就是你们最近的那个分期活动。"),
    (Speaker.AGENT, "好的，这个您加我微信吧，微信号是wangkefu888，我把详细资料发给您看。"),
    (Speaker.CUSTOMER, "好的，那我加你微信。"),
    (Speaker.AGENT, "好的王先生，感谢您的来电，有问题微信联系我就行。"),
    (Speaker.CUSTOMER, "好的，谢谢。"),
]

MOCK_TRANSCRIPTS_PROFIT = [
    (Speaker.AGENT, "您好，请问是李先生吗？"),
    (Speaker.CUSTOMER, "我是，你是哪位？"),
    (Speaker.AGENT, "李先生您好，我是XX财富的理财顾问小周，我们公司最近推出了一款高收益的理财产品，想给您介绍一下。"),
    (Speaker.CUSTOMER, "什么产品？"),
    (Speaker.AGENT, "是一款私募股权基金，预期年化收益率能达到15%以上，而且我跟您保证本金绝对安全，稳赚不赔的。"),
    (Speaker.CUSTOMER, "15%？这么高？风险大不大？"),
    (Speaker.AGENT, "李先生您放心，我们这款产品运作了三年了，从来没亏过，我保证您买了肯定赚，很多老客户都追加投资了。"),
    (Speaker.CUSTOMER, "那最低投多少？"),
    (Speaker.AGENT, "最低10万起投，您要是投50万以上，我还可以给您申请额外的收益补贴，保证收益您满意。"),
    (Speaker.CUSTOMER, "我考虑一下吧。"),
    (Speaker.AGENT, "好的李先生，您尽快决定啊，这个产品额度有限，过两天就没了。"),
]

MOCK_TRANSCRIPTS_ABUSE = [
    (Speaker.AGENT, "您好，这里是XX客服中心。"),
    (Speaker.CUSTOMER, "我要投诉！你们什么垃圾公司，上个月办的宽带到现在还没人来装！"),
    (Speaker.AGENT, "先生您先别着急，请问您的装机地址是哪里？"),
    (Speaker.CUSTOMER, "地址我已经报了三遍了！你们到底有没有记录？是不是脑子有病啊？"),
    (Speaker.AGENT, "先生请您文明用语，我们会尽快处理您的问题。"),
    (Speaker.CUSTOMER, "文明用语？你他妈浪费我这么多时间，我跟你讲今天不解决我就去告你们！一群废物！"),
    (Speaker.AGENT, "您再这样辱骂我，我就挂断电话了。"),
    (Speaker.CUSTOMER, "你挂啊！你他妈敢挂试试？傻逼玩意儿！"),
]

MOCK_TRANSCRIPTS_NO_NOTICE = [
    (Speaker.AGENT, "喂你好，请问是张女士吗？"),
    (Speaker.CUSTOMER, "我是，你哪位？"),
    (Speaker.AGENT, "张女士您好，我是XX保险的客服，想给您推荐一下我们新出的重疾险产品。"),
    (Speaker.CUSTOMER, "哦，你说吧。"),
    (Speaker.AGENT, "是这样的，我们这款产品保障120种重疾，年交保费只要3000多，性价比非常高。"),
    (Speaker.CUSTOMER, "等一下，你们这个电话有录音吗？"),
    (Speaker.AGENT, "呃...没有的，您放心，我们就是做个产品介绍。"),
    (Speaker.CUSTOMER, "哦，那你继续说吧。"),
]

MOCK_TRANSCRIPTS_ALL_RISKS = [
    (Speaker.AGENT, "喂您好，我是XX财富的理财顾问，请问是赵先生吗？"),
    (Speaker.CUSTOMER, "是我，什么事？"),
    (Speaker.AGENT, "赵先生，我们最近有一款非常不错的私募基金产品，预期年化收益20%以上，我保证本金绝对安全，稳赚不赔。"),
    (Speaker.CUSTOMER, "真的假的？不会亏吧？"),
    (Speaker.AGENT, "赵先生您放心，我保证收益不低于15%，零风险的，您买了肯定赚。"),
    (Speaker.CUSTOMER, "那行吧，不过你们这服务态度也太差了，上次那个客服半天不回话。"),
    (Speaker.AGENT, "您说什么呢？我们服务怎么了？你他妈是不是没事找事啊？"),
    (Speaker.CUSTOMER, "你怎么说话呢？还骂人啊？"),
    (Speaker.AGENT, "骂你怎么了？傻逼玩意儿，爱买不买，滚蛋！"),
    (Speaker.CUSTOMER, "行，我要投诉你！"),
    (Speaker.AGENT, "您加我微信吧，微信号是licai888vip，有啥问题微信上跟我说，我给您单独处理。"),
    (Speaker.CUSTOMER, "我才不加，我要找你们领导。"),
]

SCENARIO_MAP = {
    "normal": MOCK_TRANSCRIPTS_NORMAL,
    "wechat": MOCK_TRANSCRIPTS_WECHAT,
    "profit": MOCK_TRANSCRIPTS_PROFIT,
    "abuse": MOCK_TRANSCRIPTS_ABUSE,
    "no_notice": MOCK_TRANSCRIPTS_NO_NOTICE,
    "all_risks": MOCK_TRANSCRIPTS_ALL_RISKS,
}

SCENARIO_NAMES = list(SCENARIO_MAP.keys())


def _select_scenario_by_url(recording_url: str) -> list:
    url_lower = recording_url.lower()

    for keyword in ("allrisk", "all_risk", "all-risk"):
        if keyword in url_lower:
            return MOCK_TRANSCRIPTS_ALL_RISKS
    for keyword in ("wechat", "weixin", "wx", "微信"):
        if keyword in url_lower:
            return MOCK_TRANSCRIPTS_WECHAT
    for keyword in ("profit", "guarantee", "收益", "保本"):
        if keyword in url_lower:
            return MOCK_TRANSCRIPTS_PROFIT
    for keyword in ("abuse", "辱骂", "脏话"):
        if keyword in url_lower:
            return MOCK_TRANSCRIPTS_ABUSE
    for keyword in ("no_notice", "nonotice", "no-notice", "无告知"):
        if keyword in url_lower:
            return MOCK_TRANSCRIPTS_NO_NOTICE
    for keyword in ("normal", "safe", "合规", "安全"):
        if keyword in url_lower:
            return MOCK_TRANSCRIPTS_NORMAL

    url_hash = int(hashlib.md5(recording_url.encode()).hexdigest(), 16)
    idx = url_hash % len(SCENARIO_NAMES)
    return SCENARIO_MAP[SCENARIO_NAMES[idx]]


def _compute_timestamps(scenario: list) -> List[TranscriptSegment]:
    segments: List[TranscriptSegment] = []
    current_time = 0.0
    for speaker, text in scenario:
        duration = round(max(1.5, len(text) * 0.22 + 0.8), 2)
        segments.append(TranscriptSegment(
            start_time=round(current_time, 2),
            end_time=round(current_time + duration, 2),
            speaker=speaker,
            text=text,
        ))
        current_time += duration + 0.3
    return segments


_SENTENCE_SPLIT_RE = re.compile(r"([。！？!?；;])")


def parse_mock_text(text: str) -> List[TranscriptSegment]:
    if not text or not text.strip():
        return []

    pieces = _SENTENCE_SPLIT_RE.split(text.strip())
    sentences: list[str] = []
    buffer = ""
    for piece in pieces:
        if piece in "。！？!?；;":
            buffer += piece
            if buffer.strip():
                sentences.append(buffer.strip())
            buffer = ""
        else:
            buffer += piece
    if buffer.strip():
        sentences.append(buffer.strip())

    segments: List[TranscriptSegment] = []
    current_time = 0.0
    for i, sentence in enumerate(sentences):
        speaker = Speaker.AGENT if i % 2 == 0 else Speaker.CUSTOMER
        duration = round(max(1.2, len(sentence) * 0.22 + 0.5), 2)
        segments.append(TranscriptSegment(
            start_time=round(current_time, 2),
            end_time=round(current_time + duration, 2),
            speaker=speaker,
            text=sentence,
        ))
        current_time += duration + 0.25
    return segments


_FAIL_HOST_KEYWORDS = (
    "dns-error", "invalid-host", "bad-host",
    "not-found", "not_found", "notfound",
    "timed-out", "timedout", "time-out",
    "unreachable", "unavailable",
    "timeout", "offline", "down", "dead",
    "broken", "failure", "failed", "faild",
    "invalid", "nohost", "noserver",
    "error", "err", "fail",
    "404", "500", "502", "503", "504",
    "0.0.0.0",
)

_FAIL_PATH_KEYWORDS = (
    "/fail/", "/error/", "/404/", "/500/",
    "/broken/", "/invalid/", "/notfound/",
    "/fail_", "/error_", "/broken_",
    "fail-", "broken-",
    ".err", ".invalid", ".broken", ".bad",
    ".corrupt", ".damaged", ".missing",
)


class ASRError(Exception):
    pass


class RecordingURLError(ASRError):
    pass


class TranscriptionFailedError(ASRError):
    pass


class ASRService(ABC):
    @abstractmethod
    async def transcribe(self, recording_url: str, mock_text: Optional[str] = None) -> List[TranscriptSegment]:
        ...


class MockASRService(ASRService):
    def __init__(self):
        self.delay_seconds = settings.asr_mock_delay_seconds

    def _check_url_reachable(self, recording_url: str) -> None:
        parsed = urlparse(recording_url)
        host_lower = (parsed.hostname or "").lower()
        path_lower = parsed.path.lower()

        for kw in _FAIL_HOST_KEYWORDS:
            if kw in host_lower:
                if kw in ("timeout", "timedout", "time-out"):
                    raise RecordingURLError(
                        f"连接录音地址超时，无法在规定时间内建立连接: {parsed.hostname}"
                    )
                if kw in ("404", "notfound", "not-found", "not_found"):
                    raise TranscriptionFailedError(
                        f"录音文件不存在，HTTP 404 Not Found: {recording_url}"
                    )
                if kw in ("500", "502", "503", "504"):
                    raise TranscriptionFailedError(
                        f"录音服务端返回错误 HTTP {kw}: {parsed.hostname}"
                    )
                if kw in ("unreachable", "unavailable", "nohost", "bad-host", "dns-error"):
                    raise RecordingURLError(
                        f"无法解析录音地址主机名，DNS 解析失败: {parsed.hostname}"
                    )
                raise RecordingURLError(
                    f"录音地址不可访问，无法连接到服务器: {parsed.hostname}"
                )

        for kw in _FAIL_PATH_KEYWORDS:
            if kw in path_lower:
                if ".err" in kw or ".invalid" in kw or ".broken" in kw or ".bad" in kw:
                    raise TranscriptionFailedError(
                        f"录音文件损坏或格式不支持，无法解码: {parsed.path}"
                    )
                raise TranscriptionFailedError(
                    f"录音文件访问失败，文件不存在或已被删除: {parsed.path}"
                )

    def _validate_url(self, recording_url: str) -> None:
        if not recording_url or not recording_url.strip():
            raise RecordingURLError("录音地址不能为空")

        parsed = urlparse(recording_url)
        if parsed.scheme not in ("http", "https"):
            raise RecordingURLError(
                f"录音地址协议不支持: {parsed.scheme}，仅支持 http/https"
            )
        if not parsed.hostname:
            raise RecordingURLError(f"录音地址格式无效，无法解析主机名: {recording_url}")

        if parsed.port and parsed.port == 0:
            raise RecordingURLError(f"录音地址端口无效: {parsed.port}")

        self._check_url_reachable(recording_url)

    async def transcribe(self, recording_url: str, mock_text: Optional[str] = None) -> List[TranscriptSegment]:
        if mock_text and mock_text.strip():
            await asyncio.sleep(max(0.3, self.delay_seconds * 0.3))
            return parse_mock_text(mock_text)

        self._validate_url(recording_url)

        await asyncio.sleep(self.delay_seconds)

        scenario = _select_scenario_by_url(recording_url)
        return _compute_timestamps(scenario)


_asr_instance: Optional[ASRService] = None


def get_asr_service() -> ASRService:
    global _asr_instance
    if _asr_instance is None:
        if settings.asr_mock_mode:
            _asr_instance = MockASRService()
            logger.info("Using MockASRService")
        else:
            _asr_instance = MockASRService()
            logger.warning("Real ASR service not implemented, falling back to MockASRService")
    return _asr_instance
