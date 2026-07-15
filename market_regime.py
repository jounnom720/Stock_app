# -*- coding: utf-8 -*-
"""
market_regime.py

[레이어 1] 시장 국면 분류 모듈

코스피(또는 다른 지수)의 당일 등락률을 기준으로 시장 국면을 판단합니다.
이 국면 값은 이후 레이어 2(종목 지표)와 레이어 3(조합 문장 생성)에서
"같은 지표라도 시장 상황에 따라 다르게 해석"하는 데 사용됩니다.

사용 예:
    from market_regime import classify_market_regime

    result = classify_market_regime(kospi_change_pct=-8.95)
    print(result["regime"])       # "패닉_급락장"
    print(result["description"])  # "패닉성 급락장"
"""

from dataclasses import dataclass


# ---------------------------------------------------------
# 국면 구간 기준 (필요시 이 부분만 조정하면 전체 로직에 반영됨)
# ---------------------------------------------------------
REGIME_THRESHOLDS = {
    "패닉_급락장": (-100.0, -5.0),   # -5% 이하
    "조정_국면": (-5.0, -3.0),       # -5% ~ -3%
    "변동성_확대_하락": (-3.0, -1.0), # -3% ~ -1%
    "평상시": (-1.0, 1.0),           # -1% ~ +1%
    "변동성_확대_상승": (1.0, 3.0),   # +1% ~ +3%
    "급등장": (3.0, 100.0),          # +3% 이상
}

# 각 국면에 대한 사람이 읽는 설명 문구
REGIME_DESCRIPTIONS = {
    "패닉_급락장": "패닉성 급락장",
    "조정_국면": "단기 조정 국면",
    "변동성_확대_하락": "변동성이 다소 커진 하락 장세",
    "평상시": "평이한 시장 흐름",
    "변동성_확대_상승": "변동성이 다소 커진 상승 장세",
    "급등장": "이례적인 급등장",
}


@dataclass
class MarketRegimeResult:
    """시장 국면 분류 결과를 담는 데이터 클래스"""
    regime: str            # 예: "패닉_급락장"
    description: str       # 예: "패닉성 급락장"
    change_pct: float      # 입력받은 등락률 그대로
    index_name: str        # 어떤 지수 기준인지 (예: "코스피")

    def to_dict(self) -> dict:
        return {
            "regime": self.regime,
            "description": self.description,
            "change_pct": self.change_pct,
            "index_name": self.index_name,
        }


def classify_market_regime(
    change_pct: float,
    index_name: str = "코스피",
) -> MarketRegimeResult:
    """
    지수 등락률을 받아 시장 국면을 분류합니다.

    Args:
        change_pct: 당일 등락률 (예: -8.95는 -8.95%를 의미)
        index_name: 지수 이름 (기본값: "코스피"). 코스닥 등 다른 지수에도 사용 가능

    Returns:
        MarketRegimeResult: 국면, 설명, 원본 등락률, 지수명을 담은 결과 객체

    Raises:
        ValueError: change_pct가 숫자가 아니거나 비정상적으로 큰 값(±100% 초과)일 경우
    """
    if not isinstance(change_pct, (int, float)):
        raise ValueError(f"change_pct는 숫자여야 합니다. 입력값: {change_pct!r}")

    if abs(change_pct) > 100:
        raise ValueError(
            f"등락률이 비정상적으로 큽니다: {change_pct}%. "
            "퍼센트(%) 단위로 입력했는지 확인해주세요 (예: -8.95, 8.95 등 소수)."
        )

    for regime, (lower, upper) in REGIME_THRESHOLDS.items():
        # 마지막 구간(급등장)은 upper 값도 포함하도록 처리
        if regime == "급등장":
            if change_pct >= lower:
                matched_regime = regime
                break
        else:
            if lower <= change_pct < upper:
                matched_regime = regime
                break
    else:
        # 이론상 도달하지 않지만, 안전장치로 평상시 처리
        matched_regime = "평상시"

    return MarketRegimeResult(
        regime=matched_regime,
        description=REGIME_DESCRIPTIONS[matched_regime],
        change_pct=change_pct,
        index_name=index_name,
    )


def is_panic_regime(result: MarketRegimeResult) -> bool:
    """패닉/급락 국면인지 간단히 확인하는 헬퍼 함수"""
    return result.regime == "패닉_급락장"


def is_surge_regime(result: MarketRegimeResult) -> bool:
    """급등 국면인지 간단히 확인하는 헬퍼 함수"""
    return result.regime == "급등장"


# ---------------------------------------------------------
# 간단한 동작 확인용 (직접 실행 시에만 동작, import 시에는 실행 안 됨)
# ---------------------------------------------------------
if __name__ == "__main__":
    test_cases = [-8.95, -4.2, -2.1, 0.3, 1.8, 4.5]

    for pct in test_cases:
        result = classify_market_regime(pct, index_name="코스피")
        print(f"등락률 {pct:+.2f}% → 국면: {result.regime} ({result.description})")
