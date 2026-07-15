# -*- coding: utf-8 -*-
"""
insight_generator.py

[레이어 3] 국면 + 지표 + 투자자 동향 조합 → 문장 생성 모듈

레이어 1(market_regime.py)의 "시장 국면",
레이어 2(stock_indicators.py)의 "종목 지표",
그리고 투자자 동향(investor_trend.py)의 "외국인/기관/개인 순매수 추세"를
함께 받아서, 사람이 읽기 편한 인사이트 문장을 만들어냅니다.

핵심 원칙: AI가 즉흥적으로 문장을 짓는 게 아니라,
이미 계산된 숫자(국면, 이격도, RSI, 거래량배율, 투자자별 순매수)를 근거로
미리 정해둔 문장 틀에 끼워 넣는 방식입니다. (완전 무료, 재현 가능)

※ 체결강도는 실시간 틱 데이터가 필요해 이 모듈에는 포함하지 않았습니다.

사용 예:
    from market_regime import classify_market_regime
    from stock_indicators import get_stock_indicators
    from investor_trend import get_investor_trend
    from insight_generator import generate_insight

    regime = classify_market_regime(-8.95, index_name="코스피")
    indicators = get_stock_indicators("005930")
    investor = get_investor_trend("005930")
    print(generate_insight(regime, indicators, investor).summary)
"""

from dataclasses import dataclass
from typing import Optional


# ---------------------------------------------------------
# 지표 조건 판단 기준 (필요시 이 부분만 조정)
# ---------------------------------------------------------
VOLUME_SURGE_RATIO = 2.0     # 이 배율 이상이면 "거래량 급증"
VOLUME_DROP_RATIO = 0.5      # 이 배율 이하면 "거래량 급감"

RSI_OVERBOUGHT = 70.0        # 이 이상이면 "과매수"
RSI_OVERSOLD = 30.0          # 이 이하면 "과매도"

MA_DEVIATION_HIGH = 10.0     # 이동평균 대비 +10% 이상이면 "상방 과열"
MA_DEVIATION_LOW = -10.0     # 이동평균 대비 -10% 이하면 "하방 이탈"

INVESTOR_STREAK_MIN_DAYS = 2  # 이 일수 이상 연속돼야 코멘트로 언급


# ---------------------------------------------------------
# 국면별 "패닉/급등" 여부 그룹 (거래량 해석 시 사용)
# ---------------------------------------------------------
PANIC_REGIMES = {"패닉_급락장", "조정_국면"}
SURGE_REGIMES = {"급등장"}


# ---------------------------------------------------------
# 거래량 문장 템플릿 (국면에 따라 같은 "급증"도 다르게 해석)
# ---------------------------------------------------------
def _volume_sentence(volume_ratio: float, regime: str, index_name: str,
                      regime_change_pct: float) -> Optional[str]:
    if volume_ratio >= VOLUME_SURGE_RATIO:
        if regime in PANIC_REGIMES:
            return (
                f"오늘처럼 {index_name}가 {regime_change_pct:+.2f}%까지 급락한 패닉장에서는 "
                f"평소 대비 거래량이 {volume_ratio}배 폭증하는 게 자연스러운 현상으로, "
                f"비정상적인 수치는 아닌 것으로 보입니다."
            )
        if regime in SURGE_REGIMES:
            return (
                f"매수세가 집중되며 거래량이 평소 대비 {volume_ratio}배 급증했습니다. "
                f"단기 과열 가능성에 유의하시기 바랍니다."
            )
        return (
            f"거래량이 평소 대비 {volume_ratio}배 증가했습니다. "
            f"특별한 이슈가 있는지 확인해보시는 게 좋겠습니다."
        )

    if volume_ratio <= VOLUME_DROP_RATIO:
        return (
            f"거래량이 평소 대비 {volume_ratio}배 수준으로 뜸한 편으로, "
            f"시장의 관심이 다소 줄어든 상태로 보입니다."
        )

    return None  # 평범한 거래량 수준이면 별도 코멘트 생략


def _rsi_sentence(rsi: float) -> Optional[str]:
    if rsi >= RSI_OVERBOUGHT:
        return (
            f"RSI가 {rsi}로 과매수 구간에 진입해, 단기적으로 조정 가능성을 "
            f"염두에 두시는 게 좋겠습니다."
        )
    if rsi <= RSI_OVERSOLD:
        return (
            f"RSI가 {rsi}로 과매도 구간에 있어, 단기 반등 가능성도 함께 "
            f"참고하실 만합니다."
        )
    return None


def _ma_deviation_sentence(ma_deviation_pct: float) -> Optional[str]:
    if ma_deviation_pct >= MA_DEVIATION_HIGH:
        return (
            f"20일 이동평균 대비 {ma_deviation_pct:+.2f}% 위에 있어, "
            f"단기적으로 많이 오른 상태입니다."
        )
    if ma_deviation_pct <= MA_DEVIATION_LOW:
        return (
            f"20일 이동평균 대비 {ma_deviation_pct:+.2f}% 아래에 있어, "
            f"단기 하락폭이 다소 큰 편입니다."
        )
    return None


def _investor_sentences(investor_trends: dict) -> list:
    """
    투자자 유형(외국인/기관/개인)별 순매수 연속 추세를 문장으로 변환합니다.
    INVESTOR_STREAK_MIN_DAYS 일 이상 연속된 경우에만 코멘트로 언급합니다.
    """
    sentences = []

    tone_positive = {"외국인": "수급 측면에서 긍정적입니다.",
                      "기관": "수급 측면에서 긍정적입니다.",
                      "개인": "개인 투자자들의 관심이 높아지고 있는 것으로 보입니다."}
    tone_negative = {"외국인": "단기 수급이 약한 편입니다.",
                      "기관": "단기 수급이 약한 편입니다.",
                      "개인": "개인 매도세가 이어지고 있습니다."}

    for investor_type, info in investor_trends.items():
        direction = info.get("streak_direction")
        days = info.get("streak_days", 0)

        if direction is None or days < INVESTOR_STREAK_MIN_DAYS:
            continue

        if direction == "순매수":
            tone = tone_positive.get(investor_type, "수급 측면에서 긍정적입니다.")
        else:
            tone = tone_negative.get(investor_type, "단기 수급이 약한 편입니다.")

        sentences.append(
            f"최근 {days}일간 {investor_type}이 연속 {direction} 중이라 {tone}"
        )

    return sentences


@dataclass
class InsightResult:
    """생성된 인사이트를 담는 결과 객체"""
    ticker: str
    sentences: list       # 조건에 걸린 문장들의 리스트
    summary: str          # 문장들을 하나로 합친 최종 코멘트
    has_notable_signal: bool  # 특이 신호가 하나라도 있었는지 여부
    terms_used: list       # 이 코멘트에 등장한 전문 용어 목록 (용어 설명 연동용)

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "sentences": self.sentences,
            "summary": self.summary,
            "has_notable_signal": self.has_notable_signal,
            "terms_used": self.terms_used,
        }


def generate_insight(regime_result, indicator_result, investor_result=None) -> InsightResult:
    """
    시장 국면 결과, 종목 지표 결과, (선택) 투자자 동향 결과를 조합해서
    인사이트 문장을 생성합니다.

    Args:
        regime_result: market_regime.classify_market_regime()의 반환값
        indicator_result: stock_indicators.get_stock_indicators()의 반환값
        investor_result: investor_trend.get_investor_trend()의 반환값 (선택, 없으면 투자자 코멘트 생략)

    Returns:
        InsightResult: 생성된 문장들과 최종 요약 코멘트, 사용된 용어 목록
    """
    sentences = []
    terms_used = []

    if indicator_result.data_points == 0:
        return InsightResult(
            ticker=indicator_result.ticker,
            sentences=[],
            summary="데이터를 가져오지 못해 인사이트를 생성할 수 없습니다.",
            has_notable_signal=False,
            terms_used=[],
        )

    if indicator_result.volume_ratio is not None:
        s = _volume_sentence(
            indicator_result.volume_ratio,
            regime_result.regime,
            regime_result.index_name,
            regime_result.change_pct,
        )
        if s:
            sentences.append(s)
            terms_used.append("거래량 배율")
            terms_used.append("시장 국면")

    if indicator_result.rsi is not None:
        s = _rsi_sentence(indicator_result.rsi)
        if s:
            sentences.append(s)
            terms_used.append("RSI")
            terms_used.append("과매수" if indicator_result.rsi >= RSI_OVERBOUGHT else "과매도")

    if indicator_result.ma_deviation_pct is not None:
        s = _ma_deviation_sentence(indicator_result.ma_deviation_pct)
        if s:
            sentences.append(s)
            terms_used.append("이동평균 이격도")

    if investor_result is not None and investor_result.data_available:
        investor_sentences = _investor_sentences(investor_result.trends)
        if investor_sentences:
            sentences.extend(investor_sentences)
            terms_used.append("순매수")

    has_notable_signal = len(sentences) > 0

    if not sentences:
        summary = "특이 신호 없이 평이한 흐름을 보이고 있습니다."
    else:
        summary = " ".join(sentences)

    # 중복 제거 (순서 유지)
    terms_used = list(dict.fromkeys(terms_used))

    return InsightResult(
        ticker=indicator_result.ticker,
        sentences=sentences,
        summary=summary,
        has_notable_signal=has_notable_signal,
        terms_used=terms_used,
    )


# ---------------------------------------------------------
# 간단한 동작 확인용 (직접 실행 시에만 동작)
# ---------------------------------------------------------
if __name__ == "__main__":
    from market_regime import classify_market_regime
    from stock_indicators import StockIndicatorResult
    from investor_trend import InvestorTrendResult

    # 시나리오: 패닉장 + 거래량 급증 + 외국인 3일 연속 순매도
    regime1 = classify_market_regime(-8.95, index_name="코스피")
    fake_indicator1 = StockIndicatorResult(
        ticker="005930",
        latest_date="2026-07-15",
        latest_close=279000,
        ma_deviation_pct=-3.2,
        rsi=45.0,
        volume_ratio=2.8,
        data_points=41,
    )
    fake_investor1 = InvestorTrendResult(
        ticker="005930",
        latest_date="2026-07-15",
        trends={
            "외국인": {"latest_net": -15000000000, "streak_direction": "순매도", "streak_days": 3},
            "기관": {"latest_net": 5000000000, "streak_direction": "순매수", "streak_days": 2},
            "개인": {"latest_net": 10000000000, "streak_direction": "순매수", "streak_days": 4},
        },
        data_available=True,
    )
    result1 = generate_insight(regime1, fake_indicator1, fake_investor1)
    print("=== 시나리오: 패닉장 + 거래량 급증 + 투자자별 동향 ===")
    print(result1.summary)
    print()
    print("사용된 용어:", result1.terms_used)
