from typing import List, Tuple
import re
import logging

from .schemas import (
    TranscriptSegment,
    RiskFragment,
    RiskCategory,
    RiskLevel,
    Speaker,
)

logger = logging.getLogger(__name__)


WECHAT_KEYWORDS = [
    "加微信", "加我微信", "加个微信", "加你微信", "加一下微信",
    "微信号", "我的微信", "你微信", "微信多少", "微信联系",
    "weixin", "wechat", "wx", "V信", "威信",
    "加个V", "加V", "扫二维码", "扫码加",
]

PROFIT_GUARANTEE_KEYWORDS = [
    "保证收益", "保证盈利", "保证赚钱", "保本保息", "保本保利",
    "稳赚不赔", "只赚不亏", "零风险", "无风险", "绝对安全",
    "保本收益", "固定收益", "承诺收益", "承诺盈利", "承诺回报",
    "百分百收益", "百分之百赚", "肯定赚", "肯定盈利",
    "最低收益", "保底收益", "不会亏", "不可能亏",
]

ABUSE_KEYWORDS = [
    "傻逼", "傻逼玩意儿", "傻逼东西", "你他妈", "他妈的",
    "草泥马", "操你妈", "废物", "垃圾", "白痴",
    "弱智", "脑子有病", "有病吧", "神经病", "脑残",
    "滚蛋", "去死", "不要脸", "畜生", "狗娘养的",
]

RECORDING_NOTICE_KEYWORDS = [
    "录音", "电话录音", "本次通话", "通话录音", "全程录音",
    "为了保证服务质量", "为了保障您的权益", "服务质量监督",
    "您的通话", "正在录音", "被录音",
]


class RiskRule:
    def __init__(
        self,
        category: RiskCategory,
        keywords: List[str],
        level: RiskLevel,
        suggestion: str,
        speaker_filter: List[Speaker] = None,
        match_any: bool = True,
        negation_keywords: List[str] = None,
    ):
        self.category = category
        self.keywords = keywords
        self.level = level
        self.suggestion = suggestion
        self.speaker_filter = speaker_filter
        self.match_any = match_any
        self.negation_keywords = negation_keywords or []

    def match(self, text: str, speaker: Speaker) -> Tuple[bool, List[str]]:
        if self.speaker_filter and speaker not in self.speaker_filter:
            return False, []

        matched = [kw for kw in self.keywords if kw.lower() in text.lower()]

        if self.negation_keywords:
            has_negation = any(nk in text for nk in self.negation_keywords)
            if has_negation:
                return False, []

        if self.match_any:
            return (len(matched) > 0), matched
        else:
            return (len(matched) == len(self.keywords)), matched


RISK_RULES: List[RiskRule] = [
    RiskRule(
        category=RiskCategory.WECHAT_SOLICITATION,
        keywords=WECHAT_KEYWORDS,
        level=RiskLevel.HIGH,
        suggestion="坐席涉嫌引导客户添加私人联系方式，违反合规规定，请立即约谈并核查实际情况。",
        speaker_filter=[Speaker.AGENT],
    ),
    RiskRule(
        category=RiskCategory.PROFIT_GUARANTEE,
        keywords=PROFIT_GUARANTEE_KEYWORDS,
        level=RiskLevel.CRITICAL,
        suggestion="坐席向客户承诺保证收益，严重违反金融监管规定，请立即启动合规调查并保存证据。",
        speaker_filter=[Speaker.AGENT],
    ),
    RiskRule(
        category=RiskCategory.VERBAL_ABUSE,
        keywords=ABUSE_KEYWORDS,
        level=RiskLevel.HIGH,
        suggestion="通话中出现辱骂性语言，请核实说话人身份并依据服务规范进行处理。",
    ),
    RiskRule(
        category=RiskCategory.MISSING_RECORDING_NOTICE,
        keywords=RECORDING_NOTICE_KEYWORDS,
        level=RiskLevel.MEDIUM,
        suggestion="通话未按规定进行录音告知，需提醒坐席严格遵守首句录音告知规范。",
        speaker_filter=[Speaker.AGENT],
        negation_keywords=["没有", "无", "不", "没"],
    ),
]


class ComplianceEngine:
    def __init__(self):
        self.rules = RISK_RULES

    def analyze_segments(self, segments: List[TranscriptSegment]) -> List[RiskFragment]:
        risks: List[RiskFragment] = []

        has_recording_notice = False
        for idx, seg in enumerate(segments):
            if idx < 3 and seg.speaker == Speaker.AGENT:
                if any(kw in seg.text for kw in RECORDING_NOTICE_KEYWORDS):
                    has_recording_notice = True
                    break

        for idx, seg in enumerate(segments):
            for rule in self.rules:
                if rule.category == RiskCategory.MISSING_RECORDING_NOTICE:
                    if idx < 3 and seg.speaker == Speaker.AGENT:
                        continue
                    if has_recording_notice:
                        continue

                is_match, matched_kw = rule.match(seg.text, seg.speaker)
                if is_match:
                    if rule.category == RiskCategory.MISSING_RECORDING_NOTICE:
                        if idx != 0 and seg.speaker != Speaker.AGENT:
                            continue
                        if "没有" in seg.text or "没" in seg.text:
                            risks.append(RiskFragment(
                                segment_index=idx,
                                original_text=seg.text,
                                speaker=seg.speaker,
                                start_time=seg.start_time,
                                end_time=seg.end_time,
                                risk_category=rule.category,
                                risk_level=rule.level,
                                matched_keywords=matched_kw if matched_kw else ["未告知录音"],
                                suggestion=rule.suggestion,
                            ))
                            break
                        continue

                    risks.append(RiskFragment(
                        segment_index=idx,
                        original_text=seg.text,
                        speaker=seg.speaker,
                        start_time=seg.start_time,
                        end_time=seg.end_time,
                        risk_category=rule.category,
                        risk_level=rule.level,
                        matched_keywords=matched_kw,
                        suggestion=rule.suggestion,
                    ))
                    break

        if not has_recording_notice and segments:
            first_agent_idx = next((i for i, s in enumerate(segments) if s.speaker == Speaker.AGENT), None)
            if first_agent_idx is not None:
                first_seg = segments[first_agent_idx]
                already_reported = any(
                    r.segment_index == first_agent_idx and r.risk_category == RiskCategory.MISSING_RECORDING_NOTICE
                    for r in risks
                )
                if not already_reported:
                    risks.append(RiskFragment(
                        segment_index=first_agent_idx,
                        original_text=first_seg.text,
                        speaker=first_seg.speaker,
                        start_time=first_seg.start_time,
                        end_time=first_seg.end_time,
                        risk_category=RiskCategory.MISSING_RECORDING_NOTICE,
                        risk_level=RiskLevel.MEDIUM,
                        matched_keywords=["未告知录音"],
                        suggestion="通话未按规定进行录音告知，需提醒坐席严格遵守首句录音告知规范。",
                    ))

        return risks


_engine_instance: ComplianceEngine | None = None


def get_compliance_engine() -> ComplianceEngine:
    global _engine_instance
    if _engine_instance is None:
        _engine_instance = ComplianceEngine()
    return _engine_instance
